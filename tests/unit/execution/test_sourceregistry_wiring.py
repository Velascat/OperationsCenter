# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Verification Gaps Round 2 — SourceRegistry wired for real (Option B).

Closes the four-revs-of-ducking from the post-extraction validation
arc: SourceRegistry was imported in `execution/binding.py` and
referenced in `execution/target.py`'s provenance docstring, but
never actually exercised on any execute path. These tests prove
the wiring works end-to-end and that the validation invariant
"if backend came from SourceRegistry, source name and SHA are
visible" is satisfied through to the trace.

Acceptance criteria (per backlog):
  (a) registry-yaml end-to-end load test
  (b) BoundExecutionTarget.provenance reflects source_name + SHA
      when registry-derived (None when not)
  (c) end-to-end propagation into record metadata + trace
  (d) failure-semantics tests (missing yaml, missing entry, etc.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.execution import BoundExecutionTargetMirror
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.binding import _provenance_from_registry
from operations_center.execution.handoff import (
    ExecutionRequestBuilder,
    ExecutionRuntimeContext,
    _bound_target_from_decision,
)
from operations_center.planning.models import (
    PlanningContext,
    ProposalDecisionBundle,
)
from operations_center.planning.proposal_builder import build_proposal


# ---------------------------------------------------------------------------
# (a) End-to-end load against the real registry/source_registry.yaml
# ---------------------------------------------------------------------------


def test_provenance_from_registry_resolves_real_kodo_entry(monkeypatch, tmp_path: Path) -> None:
    """The shipped registry/source_registry.yaml must resolve `kodo` to
    its ProtocolWarden fork with a populated expected_sha.

    Pinned because the validation revs flagged "SourceRegistry not
    exercised on live execute paths"; this is the smallest end-to-end
    proof that the binding code can in fact read the on-disk registry.
    """
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    provenance = _provenance_from_registry("kodo")

    assert provenance is not None, "kodo entry must resolve from registry/source_registry.yaml"
    assert provenance.source == "registry"
    assert provenance.repo == "ProtocolWarden/kodo"
    assert provenance.ref  # non-empty SHA — pinned per registry
    assert isinstance(provenance.patches, list)


# ---------------------------------------------------------------------------
# (b) BoundExecutionTargetMirror.provenance reflects registry vs unknown
# ---------------------------------------------------------------------------


def _bundle(backend: BackendName) -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="test",
            task_type="lint_fix",
            repo_key="r",
            clone_url="https://example.invalid/r.git",
        )
    )
    decision = LaneDecision(
        proposal_id=proposal.proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=backend,
    )
    return ProposalDecisionBundle(proposal=proposal, decision=decision)


def test_bound_target_carries_registry_provenance_when_resolvable(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    bundle = _bundle(BackendName.KODO)
    bound = _bound_target_from_decision(bundle, runtime_binding=None)

    assert bound is not None
    assert bound.backend == "kodo"
    assert bound.provenance is not None
    assert bound.provenance.source == "registry"
    assert bound.provenance.repo == "ProtocolWarden/kodo"
    assert bound.provenance.ref


def test_bound_target_provenance_none_when_registry_has_no_entry(tmp_path: Path, monkeypatch) -> None:
    """A backend the registry doesn't list (e.g. demo_stub) yields a
    bound target whose provenance is None. The mirror is still
    populated — bound_target itself is not None — but provenance is
    absent rather than fabricated. That distinction is what carries
    the "if backend came from SourceRegistry" invariant honestly."""
    monkeypatch.chdir(tmp_path)  # no registry/source_registry.yaml here

    bundle = _bundle(BackendName.DEMO_STUB)
    bound = _bound_target_from_decision(bundle, runtime_binding=None)

    assert bound is not None
    assert bound.backend == "demo_stub"
    assert bound.provenance is None


# ---------------------------------------------------------------------------
# (c) End-to-end propagation through ExecutionRequest builder
# ---------------------------------------------------------------------------


def test_request_builder_attaches_bound_target_with_provenance(monkeypatch, tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    bundle = _bundle(BackendName.KODO)
    runtime = ExecutionRuntimeContext(
        workspace_path=tmp_path,
        task_branch="auto/test",
    )
    request = ExecutionRequestBuilder().build(bundle, runtime)

    assert request.bound_target is not None
    assert request.bound_target.provenance is not None
    assert request.bound_target.provenance.source == "registry"
    assert request.bound_target.provenance.repo == "ProtocolWarden/kodo"
    assert request.bound_target.provenance.ref


def test_request_builder_bound_target_provenance_none_for_unregistered_backend(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)  # no registry visible

    bundle = _bundle(BackendName.DEMO_STUB)
    runtime = ExecutionRuntimeContext(
        workspace_path=tmp_path, task_branch="auto/test",
    )
    request = ExecutionRequestBuilder().build(bundle, runtime)

    assert request.bound_target is not None
    assert request.bound_target.provenance is None


# ---------------------------------------------------------------------------
# (c continued) End-to-end propagation through coordinator → record → trace
# ---------------------------------------------------------------------------


def test_provenance_appears_on_execution_record_metadata_and_trace(monkeypatch) -> None:
    """Closes the original validation brief's invariant:
    'if backend came from SourceRegistry, source name and SHA are visible'
    — visible on the record AND on the trace.
    """
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)

    from operations_center.contracts.common import ValidationSummary
    from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
    from operations_center.contracts.execution import ExecutionResult
    from operations_center.execution.coordinator import ExecutionCoordinator
    from operations_center.policy.models import PolicyDecision, PolicyStatus

    # Reuse the existing coordinator test fixtures.
    from tests.unit.execution.test_coordinator import (
        _RecordingAdapter,
        _Registry,
        _StubPolicyEngine,
        _runtime,
    )

    bundle = _bundle(BackendName.KODO)
    result = ExecutionResult(
        run_id=bundle.proposal.proposal_id,  # arbitrary
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )
    adapter = _RecordingAdapter(result)
    coordinator = ExecutionCoordinator(
        adapter_registry=_Registry(adapter),
        policy_engine=_StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, _runtime())

    # Record metadata carries the provenance block.
    prov_meta = outcome.record.metadata.get("provenance")
    assert prov_meta is not None, "provenance must surface on record.metadata"
    assert prov_meta["source"] == "registry"
    assert prov_meta["repo"] == "ProtocolWarden/kodo"
    assert prov_meta["ref"]

    # Trace forwards the same block.
    assert outcome.trace.provenance == prov_meta


# ---------------------------------------------------------------------------
# (d) Failure semantics — registry missing / entry missing
# ---------------------------------------------------------------------------


def test_provenance_returns_none_when_registry_yaml_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _provenance_from_registry("kodo") is None


def test_provenance_returns_none_when_entry_missing(monkeypatch) -> None:
    """SourceRegistry raises SourceNotFoundError; the binding swallows
    it and returns None so dispatch degrades gracefully."""
    repo_root = Path(__file__).resolve().parents[3]
    monkeypatch.chdir(repo_root)
    assert _provenance_from_registry("definitely-not-a-registered-backend") is None


def test_provenance_returns_none_when_yaml_malformed(tmp_path: Path, monkeypatch) -> None:
    """A malformed yaml at the canonical path is degraded, not crashing."""
    registry_dir = tmp_path / "registry"
    registry_dir.mkdir()
    (registry_dir / "source_registry.yaml").write_text(
        "this: is: not: valid: yaml: at: all\n", encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    assert _provenance_from_registry("kodo") is None


def test_bound_target_from_decision_does_not_crash_on_registry_failure(
    tmp_path: Path, monkeypatch,
) -> None:
    """Even with no registry on disk, the request builder produces a
    bound_target — provenance is None but the lane/backend mirror
    fields still resolve."""
    monkeypatch.chdir(tmp_path)

    bundle = _bundle(BackendName.KODO)
    bound = _bound_target_from_decision(bundle, runtime_binding=None)

    assert isinstance(bound, BoundExecutionTargetMirror)
    assert bound.backend == "kodo"
    assert bound.lane == "aider_local"
    assert bound.provenance is None
