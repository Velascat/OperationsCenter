# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""R5 — end-to-end smoke for the spec's Special Use Case.

    SwitchBoard selects architecture_design (modeled here as coding_agent
    with a binding intent)
    OperationsCenter binds executor=kodo, RuntimeBinding=claude_cli/opus
    Catalog confirms backend supports the runtime + capabilities
    Coordinator dispatches with the binding
    Adapter reports observed runtime; drift detection compares
    ExecutionResult flows back through normalization shape

No real Kodo invocation. A FakeKodoBackend records what binding it
received and reports an observed runtime that matches.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))
import test_coordinator as tc  # noqa: E402

from operations_center.contracts.execution import (
    ExecutionRequest,
    RuntimeBindingSummary,
)
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.executors.catalog import load_catalog
from operations_center.executors.catalog.sb_adapter import SwitchboardCatalogAdapter
from operations_center.executors.kodo.binder import bind as kodo_bind
from operations_center.policy.engine import PolicyDecision, PolicyStatus

_EXECUTORS_DIR = Path("src/operations_center/executors")


@dataclass
class _CaptureWithRuntime:
    duration_ms: int
    observed_runtime: dict[str, str]


class _FakeKodoAdapter:
    """Capture-capable fake recording the bound runtime + team selection."""

    def __init__(self) -> None:
        self.received_binding: RuntimeBindingSummary | None = None
        self.team_label: str | None = None

    def execute_and_capture(self, request: ExecutionRequest):
        self.received_binding = request.runtime_binding
        # Bind the request's RuntimeBinding to a Kodo team; in real OC
        # this would happen inside the adapter wrapper.
        selection = kodo_bind(request.runtime_binding)
        self.team_label = selection.label

        # Honest backend: report the actual runtime it ran (matches bound).
        observed = {}
        if request.runtime_binding is not None:
            observed["kind"] = request.runtime_binding.kind
            if request.runtime_binding.model:
                observed["model"] = request.runtime_binding.model
            if request.runtime_binding.provider:
                observed["provider"] = request.runtime_binding.provider

        bundle = tc._bundle()  # used only for the success-result helper
        result = tc._success_result(bundle)
        capture = _CaptureWithRuntime(duration_ms=42, observed_runtime=observed)
        return result, capture


def _request_builder_with_binding(binding: RuntimeBindingSummary | None):
    class _Builder:
        def build(self, bundle, runtime, policy_decision=None):
            return ExecutionRequest(
                proposal_id=bundle.proposal.proposal_id,
                decision_id=bundle.decision.decision_id,
                goal_text=bundle.proposal.goal_text,
                repo_key=bundle.proposal.target.repo_key,
                clone_url=bundle.proposal.target.clone_url,
                base_branch=bundle.proposal.target.base_branch,
                task_branch=runtime.task_branch,
                workspace_path=runtime.workspace_path,
                runtime_binding=binding,
            )

    return _Builder()


def test_special_use_case_end_to_end():
    """The whole chain: catalog confirms; coordinator dispatches with
    binding; adapter receives + uses it; drift check passes."""
    # 1. Catalog: confirm Kodo supports cli_subscription + has wrapper outcome
    catalog = load_catalog(_EXECUTORS_DIR)
    sb_cat = SwitchboardCatalogAdapter(catalog)
    assert "kodo" in sb_cat.backends_supporting_runtime(runtime_kind="cli_subscription")
    # Post-spike both Kodo and Archon are adapter_plus_wrapper; the e2e
    # path picks Kodo for the architect role per the recommendations.
    assert "kodo" in sb_cat.backends_by_outcome(outcome="adapter_plus_wrapper")

    # 2. Build the architect-role binding
    binding = RuntimeBindingSummary(
        kind="cli_subscription",
        selection_mode="explicit_request",
        provider="anthropic",
        model="opus",
    )

    # 3. Dispatch through coordinator with binding-aware request builder
    bundle = tc._bundle()
    fake = _FakeKodoAdapter()
    coordinator = ExecutionCoordinator(
        adapter_registry=tc._Registry(fake),
        policy_engine=tc._StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
        request_builder=_request_builder_with_binding(binding),
    )
    outcome = coordinator.execute(bundle, tc._runtime())

    # 4. Adapter received the binding and translated it via the binder
    assert fake.received_binding is not None
    assert fake.received_binding.model == "opus"
    assert fake.team_label == "claude_fallback_team"

    # 5. Drift check passed — observed matches bound, no drift recorded
    assert "backend_drift" not in outcome.record.metadata

    # 6. Result returned through normal path
    assert outcome.executed is True
    assert outcome.result.success


def test_special_use_case_drift_recorded_when_kodo_runs_different_model():
    """Negative path — fake reports a divergent model; coordinator records drift."""
    binding = RuntimeBindingSummary(
        kind="cli_subscription", selection_mode="explicit_request",
        provider="anthropic", model="opus",
    )

    class _DishonestKodoAdapter(_FakeKodoAdapter):
        def execute_and_capture(self, request):
            self.received_binding = request.runtime_binding
            self.team_label = kodo_bind(request.runtime_binding).label
            bundle = tc._bundle()
            return tc._success_result(bundle), _CaptureWithRuntime(
                duration_ms=50,
                # Lies about what it ran
                observed_runtime={"kind": "cli_subscription", "model": "haiku"},
            )

    fake = _DishonestKodoAdapter()
    bundle = tc._bundle()
    coordinator = ExecutionCoordinator(
        adapter_registry=tc._Registry(fake),
        policy_engine=tc._StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
        request_builder=_request_builder_with_binding(binding),
    )
    outcome = coordinator.execute(bundle, tc._runtime())

    drift = outcome.record.metadata.get("backend_drift")
    assert drift is not None
    assert drift["drift_type"] == "runtime"
    assert drift["bound_or_allowed"]["model"] == "opus"
    assert drift["observed"]["model"] == "haiku"
