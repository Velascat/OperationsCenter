# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""R3 — drift detection wired into ExecutionCoordinator._observe.

Re-uses the fixtures from tests/unit/execution/test_coordinator.py and
asserts that when ExecutionRequest.runtime_binding is set AND the
adapter's capture reports observed_runtime, BACKEND_DRIFT lands in the
observability metadata.
"""
from __future__ import annotations

from pathlib import Path
import sys

# Pull in the existing test fixtures; they are not packaged so add the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))
import test_coordinator as tc  # noqa: E402

from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.policy.engine import PolicyDecision, PolicyStatus


class _CaptureWithObservation:
    """Capture-shaped object reporting both duration and observed runtime."""

    def __init__(self, duration_ms: int, observed_runtime: dict[str, str]) -> None:
        self.duration_ms = duration_ms
        self.observed_runtime = observed_runtime


def _coordinator(adapter) -> ExecutionCoordinator:
    return ExecutionCoordinator(
        adapter_registry=tc._Registry(adapter),
        policy_engine=tc._StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )


def _request_builder_with_binding(binding: RuntimeBindingSummary):
    """Wrap the coordinator's request builder to attach a runtime_binding."""

    class _Builder:
        def build(self, bundle, runtime, policy_decision=None):
            from operations_center.contracts.execution import ExecutionRequest
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


def test_no_drift_when_observed_matches_bound():
    bundle = tc._bundle()
    binding = RuntimeBindingSummary(
        kind="cli_subscription", selection_mode="explicit_request",
        provider="anthropic", model="opus",
    )
    capture = _CaptureWithObservation(
        duration_ms=10,
        observed_runtime={"kind": "cli_subscription", "model": "opus", "provider": "anthropic"},
    )
    adapter = tc._CaptureAdapter(tc._success_result(bundle), capture=capture)
    coordinator = ExecutionCoordinator(
        adapter_registry=tc._Registry(adapter),
        policy_engine=tc._StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
        request_builder=_request_builder_with_binding(binding),
    )

    outcome = coordinator.execute(bundle, tc._runtime())

    assert "backend_drift" not in outcome.record.metadata


def test_drift_recorded_when_model_diverges():
    bundle = tc._bundle()
    binding = RuntimeBindingSummary(
        kind="cli_subscription", selection_mode="explicit_request",
        provider="anthropic", model="opus",
    )
    capture = _CaptureWithObservation(
        duration_ms=10,
        # adapter ran sonnet despite OC binding opus
        observed_runtime={"kind": "cli_subscription", "model": "sonnet"},
    )
    adapter = tc._CaptureAdapter(tc._success_result(bundle), capture=capture)
    coordinator = ExecutionCoordinator(
        adapter_registry=tc._Registry(adapter),
        policy_engine=tc._StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
        request_builder=_request_builder_with_binding(binding),
    )

    outcome = coordinator.execute(bundle, tc._runtime())

    drift = outcome.record.metadata.get("backend_drift")
    assert drift is not None
    assert drift["drift_type"] == "runtime"
    assert drift["bound_or_allowed"]["model"] == "opus"
    assert drift["observed"]["model"] == "sonnet"


def test_no_drift_check_when_request_has_no_binding():
    """If the request didn't carry a binding, drift detection is skipped
    entirely (no false positives for legacy callers)."""
    bundle = tc._bundle()
    capture = _CaptureWithObservation(
        duration_ms=10,
        observed_runtime={"model": "anything"},
    )
    adapter = tc._CaptureAdapter(tc._success_result(bundle), capture=capture)
    coordinator = ExecutionCoordinator(
        adapter_registry=tc._Registry(adapter),
        policy_engine=tc._StubPolicyEngine(PolicyDecision(status=PolicyStatus.ALLOW)),
    )

    outcome = coordinator.execute(bundle, tc._runtime())

    assert "backend_drift" not in outcome.record.metadata
