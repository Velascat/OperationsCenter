# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Self-healing recovery primitives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ExecutionStatus, ValidationStatus
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.backend_health import (
    BackendHealthRegistry,
    BackendHealthState,
    RecoveryStrategy,
)
from operations_center.evidence_fingerprints import evidence_fingerprint
from operations_center.queue_healing import (
    QueueHealingEngine,
    QueueHealingTask,
    QueueTransition,
)
from operations_center.entrypoints.maintenance.triage_scan import _queue_healing_actions
from operations_center.recovery import ParkedState, ParkedStateStore, should_unpark
from operations_center.recovery_policies import RecoveryBudget, RecoveryBudgetTracker


def _make_request(**overrides) -> ExecutionRequest:
    base = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="do thing",
        repo_key="repo",
        clone_url="https://example.test/repo.git",
        base_branch="main",
        task_branch="task/run-1",
        workspace_path=Path("/tmp/ws"),
    )
    base.update(overrides)
    return ExecutionRequest(**base)


def _make_result(*, request: ExecutionRequest, failure_reason: str | None = None) -> ExecutionResult:
    return ExecutionResult(
        run_id=request.run_id,
        proposal_id=request.proposal_id,
        decision_id=request.decision_id,
        status=ExecutionStatus.FAILED,
        success=False,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
        failure_reason=failure_reason,
    )


def test_backend_sigkill_transitions_to_unstable_with_cooldown():
    registry = BackendHealthRegistry(cooldown_seconds=1800)
    request = _make_request(
        runtime_binding=RuntimeBindingSummary(kind="cli_subscription", selection_mode="policy_selected")
    )
    result = _make_result(request=request, failure_reason="executor failed signal=SIGKILL")

    record, transition = registry.record_failure("kodo", result)

    assert record.state == BackendHealthState.UNSTABLE
    assert record.last_failure is not None
    assert record.last_failure.signal == "SIGKILL"
    assert record.cooldown_until is not None
    assert transition.cooldown_applied is True
    assert transition.recovery_strategy == RecoveryStrategy.REDUCE_PRESSURE


def test_queue_duplicate_deadlock_requeues_only_when_retry_safe():
    task = QueueHealingTask(
        task_id="task-1",
        title="Fix blocked duplicate",
        state="Blocked",
        duplicate_exists_in_blocked=True,
        retry_safe=True,
        retry_lineage_id="lineage-1",
    )

    decision = QueueHealingEngine().decide(task, no_consumer_can_execute=True)

    assert decision.safe is True
    assert decision.transition == QueueTransition.BLOCKED_TO_READY_FOR_AI
    assert decision.retry_lineage_id == "lineage-1"


def test_queue_healing_escalates_after_replay_budget():
    task = QueueHealingTask(
        task_id="task-1",
        title="Looping duplicate",
        state="Blocked",
        retry_safe=True,
        retry_count=2,
    )

    decision = QueueHealingEngine(max_retry_count=2).decide(
        task,
        no_consumer_can_execute=True,
    )

    assert decision.transition == QueueTransition.ESCALATE
    assert decision.escalate is True


def test_stale_blocked_task_returns_to_backlog_when_safe():
    old = datetime.now(UTC) - timedelta(hours=2)
    task = QueueHealingTask(
        task_id="task-1",
        title="Stale blocked",
        state="Blocked",
        retry_safe=True,
        updated_at=old,
    )

    decision = QueueHealingEngine(stale_blocked_seconds=3600).decide(task)

    assert decision.transition == QueueTransition.BLOCKED_TO_BACKLOG
    assert decision.safe is True


def test_triage_scan_emits_queue_healing_decision_from_structured_labels():
    now = datetime.now(UTC)
    items = [
        {
            "id": "a",
            "name": "Duplicate A",
            "state": {"name": "Blocked"},
            "labels": [
                {"name": "dedup:same"},
                {"name": "retry_safe"},
                {"name": "queue_deadlock"},
                {"name": "retry-lineage:lineage-1"},
            ],
            "updated_at": now.isoformat(),
        },
        {
            "id": "b",
            "name": "Duplicate B",
            "state": {"name": "Blocked"},
            "labels": [{"name": "dedup:same"}, {"name": "retry_safe"}],
            "updated_at": now.isoformat(),
        },
    ]

    decisions = _queue_healing_actions(items, now=now)

    assert len(decisions) == 1
    _task, _decision = decisions[0]
    assert _decision.task_id == "a"
    assert _decision.transition == QueueTransition.BLOCKED_TO_READY_FOR_AI
    assert _decision.safe is True


def test_evidence_fingerprint_ignores_timestamp_noise_and_ordering():
    first = {
        "timestamp": "2026-05-10T12:00:00Z",
        "queue": [{"id": "a", "state": "Blocked"}, {"id": "b", "state": "Ready"}],
        "exit": {"signal": "SIGKILL"},
    }
    second = {
        "timestamp": "2026-05-10T12:05:00Z",
        "queue": [{"id": "b", "state": "Ready"}, {"id": "a", "state": "Blocked"}],
        "exit": {"signal": "SIGKILL"},
    }

    assert evidence_fingerprint(first) == evidence_fingerprint(second)


def test_parked_state_unparks_on_semantic_evidence_change():
    parked = ParkedState(
        root_cause_signature="kodo_sigkill_plan_phase",
        parked_reason="backend cooldown exhausted without safe retry",
        last_evidence_hash="old",
    )

    decision = should_unpark(parked, current_evidence_hash="new")

    assert decision.parked is False
    assert decision.reason == "semantic evidence changed"


def test_parked_state_store_round_trips_metadata(tmp_path):
    store = ParkedStateStore(tmp_path / "parked.json")
    state = ParkedState(
        root_cause_signature="kodo_sigkill_plan_phase",
        parked_reason="operator action required",
        unchanged_cycles=4,
        last_evidence_hash="abc123",
    )

    store.save(state)
    loaded = store.load()

    assert loaded is not None
    assert loaded.root_cause_signature == state.root_cause_signature
    assert loaded.unchanged_cycles == 4
    assert loaded.last_evidence_hash == "abc123"


def test_recovery_budget_escalates_after_equivalent_retries():
    tracker = RecoveryBudgetTracker(RecoveryBudget(max_equivalent_retries=1))

    assert tracker.record_retry(equivalent=True).allowed is True
    decision = tracker.record_retry(equivalent=True)

    assert decision.allowed is False
    assert decision.escalate is True
