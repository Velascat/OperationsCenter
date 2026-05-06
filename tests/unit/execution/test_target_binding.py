# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 4 + 7 — bind_execution_target tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from cxrp.contracts import (
    BackendName as CxrpBackendName, ExecutionTargetEnvelope,
    ExecutorName as CxrpExecutorName, RuntimeBinding,
)
from cxrp.vocabulary.lane import LaneType
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.execution.binding import (
    InvalidRuntimeBindingError, MissingProvenanceError, PolicyViolationError,
    UnknownBackendError, UnknownExecutorError, bind_execution_target,
)
from operations_center.execution.target import (
    BackendProvenance, BoundExecutionTarget,
)


def _envelope(**overrides) -> ExecutionTargetEnvelope:
    """Helper — accepts string backend/executor and converts to typed enums.
    Schema 0.3 narrowed these on the wire; this preserves test ergonomics."""
    base = {
        "lane": LaneType.CODING_AGENT,
        "backend": "kodo",
        "executor": "claude_cli",
        "runtime_binding": None,
    }
    base.update(overrides)
    if isinstance(base["backend"], str):
        base["backend"] = CxrpBackendName(base["backend"])
    if isinstance(base["executor"], str):
        base["executor"] = CxrpExecutorName(base["executor"])
    return ExecutionTargetEnvelope(**base)


# ── Happy-path narrowing ────────────────────────────────────────────────


class TestNarrowing:
    def test_known_backend_and_executor_bind_cleanly(self):
        target = bind_execution_target(_envelope())
        assert isinstance(target, BoundExecutionTarget)
        # lane is the CxRP abstract category, kept as string
        assert target.lane == "coding_agent"
        assert target.backend == BackendName.KODO
        assert target.executor == LaneName.CLAUDE_CLI

    def test_envelope_lane_drives_oc_lane(self):
        target = bind_execution_target(_envelope(executor="codex_cli"))
        assert target.executor == LaneName.CODEX_CLI
        assert target.lane == "coding_agent"

    def test_executor_optional(self):
        target = bind_execution_target(_envelope(executor=None))
        assert target.executor is None

    def test_runtime_binding_passes_through(self):
        rb = RuntimeBinding(
            kind=RuntimeKind.CLI_SUBSCRIPTION,
            selection_mode=SelectionMode.EXPLICIT_REQUEST,
            provider="anthropic", model="opus",
        )
        target = bind_execution_target(_envelope(runtime_binding=rb))
        assert target.runtime_binding.kind == "cli_subscription"
        assert target.runtime_binding.model == "opus"


# ── Unknown values rejected at wire layer (schema 0.3) ──────────────────


class TestRejection:
    def test_unknown_backend_rejected_at_wire(self):
        """Schema 0.3 — typed enum at the wire rejects unknown backend names
        before they reach the binder. The CxRP enum raises ValueError."""
        with pytest.raises(ValueError):
            CxrpBackendName("some_future_backend")

    def test_unknown_executor_rejected_at_wire(self):
        with pytest.raises(ValueError):
            CxrpExecutorName("vibes")

    def test_missing_backend_raises_at_binder(self):
        # Backend is still required at bind time
        with pytest.raises(UnknownBackendError, match="required"):
            bind_execution_target(_envelope(backend=None))

    def test_invalid_runtime_binding_raises(self):
        class FakeBinding:
            pass
        with pytest.raises(InvalidRuntimeBindingError):
            bind_execution_target(_envelope(runtime_binding=FakeBinding()))


# ── Policy ─────────────────────────────────────────────────────────────


class TestPolicy:
    def test_policy_allow(self):
        class _AllowAll:
            def allows(self, target): return True, ""
        target = bind_execution_target(_envelope(), policy=_AllowAll())
        assert target.backend == BackendName.KODO

    def test_policy_reject_raises(self):
        class _RejectAll:
            def allows(self, target): return False, "no kodo today"
        with pytest.raises(PolicyViolationError, match="no kodo"):
            bind_execution_target(_envelope(), policy=_RejectAll())


# ── Catalog ────────────────────────────────────────────────────────────


class _StubCatalog:
    def __init__(self, *backends: str):
        self.entries = {b: object() for b in backends}


class TestCatalog:
    def test_unknown_backend_in_catalog_raises(self):
        cat = _StubCatalog("archon")  # no kodo
        with pytest.raises(UnknownBackendError, match="not present"):
            bind_execution_target(_envelope(backend="kodo"), catalog=cat)

    def test_known_backend_in_catalog_passes(self):
        cat = _StubCatalog("kodo", "archon")
        target = bind_execution_target(_envelope(backend="kodo"), catalog=cat)
        assert target.backend == BackendName.KODO


# ── Provenance from registry ────────────────────────────────────────────


class TestProvenance:
    def test_provenance_resolved_for_kodo_from_real_registry(self):
        """The shipped Velascat/kodo registry entry should resolve."""
        target = bind_execution_target(_envelope(backend="kodo"))
        assert target.provenance is not None
        assert target.provenance.source == "registry"
        assert target.provenance.repo == "Velascat/kodo"
        assert "PATCH-001" in target.provenance.patches

    def test_unforked_backend_has_no_provenance(self):
        # direct_local is not in the upstream/registry.yaml
        target = bind_execution_target(_envelope(backend="direct_local", executor=None))
        assert target.provenance is None

    def test_require_provenance_raises_for_unforked_backend(self):
        with pytest.raises(MissingProvenanceError, match="provenance entry"):
            bind_execution_target(
                _envelope(backend="direct_local", executor=None),
                require_provenance=True,
            )


# ── Mirror types serialize cleanly ──────────────────────────────────────


class TestContractMirror:
    def test_bound_target_round_trips_through_mirror(self):
        from operations_center.contracts.execution import (
            BackendProvenanceMirror, BoundExecutionTargetMirror,
            RuntimeBindingSummary,
        )
        bound = bind_execution_target(_envelope(backend="kodo"))
        mirror = BoundExecutionTargetMirror(
            lane=bound.lane,                # already a str
            backend=bound.backend.value,
            executor=bound.executor.value if bound.executor else None,
            runtime_binding=bound.runtime_binding,
            provenance=BackendProvenanceMirror(
                source=bound.provenance.source,
                repo=bound.provenance.repo,
                ref=bound.provenance.ref,
                patches=list(bound.provenance.patches),
            ) if bound.provenance else None,
        )
        # Round-trip
        d = mirror.model_dump()
        restored = BoundExecutionTargetMirror.model_validate(d)
        assert restored.backend == "kodo"
        assert restored.provenance.repo == "Velascat/kodo"
