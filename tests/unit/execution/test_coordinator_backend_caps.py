# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Coordinator → UsageStore wiring: per-backend cap enforcement at dispatch.

Covers task #31. Tests assert that:
  - The chained per-backend cap check (rate → concurrency → RAM) runs
    *after* policy approval and *before* adapter dispatch.
  - On block, no dispatch happens; a SKIPPED ExecutionResult is returned
    with BUDGET_EXHAUSTED + a reason that names the cap.
  - On allow, ``execution_started`` / ``execution_finished`` / ``execution``
    / ``execution_outcome`` events are recorded with ``backend=...`` set.
  - The finished marker fires from a finally — even when the adapter
    raises — so the concurrency cap can't deadlock on a crashed run.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.config.settings import BackendCapSettings
from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import (
    BackendName,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
    ValidationStatus,
)
from operations_center.contracts.execution import ExecutionResult
from operations_center.contracts.routing import LaneDecision
from operations_center.execution.coordinator import ExecutionCoordinator
from operations_center.execution.handoff import ExecutionRuntimeContext
from operations_center.execution.usage_store import UsageStore
from operations_center.planning.models import PlanningContext, ProposalDecisionBundle
from operations_center.planning.proposal_builder import build_proposal
from operations_center.policy.models import PolicyDecision, PolicyStatus


class _AllowPolicy:
    def evaluate(self, *_args, **_kwargs):
        return PolicyDecision(status=PolicyStatus.ALLOW)


class _RecordingAdapter:
    def __init__(self, result: ExecutionResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, request):
        self.calls += 1
        return self.result


class _CrashingAdapter:
    def execute(self, request):
        raise RuntimeError("adapter exploded")


class _Registry:
    def __init__(self, adapter) -> None:
        self._a = adapter

    def for_backend(self, _backend):
        return self._a


def _bundle() -> ProposalDecisionBundle:
    proposal = build_proposal(
        PlanningContext(
            goal_text="Fix",
            task_type="lint_fix",
            repo_key="repo-x",
            clone_url="https://example.invalid/x.git",
        )
    )
    decision = LaneDecision(
        proposal_id=proposal.proposal_id,
        selected_lane=LaneName.AIDER_LOCAL,
        selected_backend=BackendName.DIRECT_LOCAL,
    )
    return ProposalDecisionBundle(proposal=proposal, decision=decision)


def _success(bundle) -> ExecutionResult:
    return ExecutionResult(
        run_id="run-1",
        proposal_id=bundle.proposal.proposal_id,
        decision_id=bundle.decision.decision_id,
        status=ExecutionStatus.SUCCEEDED,
        success=True,
        validation=ValidationSummary(status=ValidationStatus.SKIPPED),
    )


def _runtime() -> ExecutionRuntimeContext:
    return ExecutionRuntimeContext(
        workspace_path=Path("/tmp/workspace"),
        task_branch="auto/x",
    )


# ---------------------------------------------------------------------------
# Pre-dispatch cap blocks (rate / concurrency / RAM)
# ---------------------------------------------------------------------------


class TestRateCapBlocks:
    def test_daily_rate_cap_blocks_dispatch(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(
            "OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"),
        )
        store = UsageStore()
        bundle = _bundle()
        # Pre-load 5 prior dispatches for direct_local within today's window.
        now = datetime.now(UTC)
        from datetime import timedelta
        for i in range(5):
            store.record_execution(
                role="lint_fix", task_id=f"prior-{i}", signature=f"s{i}",
                now=now - timedelta(hours=1 + i), backend="direct_local",
            )
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_AllowPolicy(),
            usage_store=store,
            backend_caps={"direct_local": BackendCapSettings(max_per_day=5)},
        )

        outcome = coord.execute(bundle, _runtime())

        assert outcome.executed is False
        assert adapter.calls == 0
        assert outcome.result.status == ExecutionStatus.SKIPPED
        assert outcome.result.failure_category == FailureReasonCategory.BUDGET_EXHAUSTED
        assert "backend_budget_exceeded" in (outcome.result.failure_reason or "")
        assert "direct_local" in (outcome.result.failure_reason or "")


class TestConcurrencyCapBlocks:
    def test_concurrency_cap_blocks_when_in_flight_at_max(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(
            "OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"),
        )
        store = UsageStore()
        bundle = _bundle()
        now = datetime.now(UTC)
        # Two unfinished dispatches in flight.
        store.record_execution_started(
            task_id="other-1", backend="direct_local", now=now,
        )
        store.record_execution_started(
            task_id="other-2", backend="direct_local", now=now,
        )
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_AllowPolicy(),
            usage_store=store,
            backend_caps={"direct_local": BackendCapSettings(max_concurrent=2)},
        )

        outcome = coord.execute(bundle, _runtime())

        assert outcome.executed is False
        assert adapter.calls == 0
        assert "backend_concurrency_exceeded" in (outcome.result.failure_reason or "")


class TestMemoryThresholdBlocks:
    def test_memory_threshold_blocks_when_below_min(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(
            "OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"),
        )
        store = UsageStore()
        # Force /proc/meminfo read to a low value
        monkeypatch.setattr(UsageStore, "available_memory_mb", staticmethod(lambda: 512))
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_AllowPolicy(),
            usage_store=store,
            backend_caps={
                "direct_local": BackendCapSettings(min_available_memory_mb=4096),
            },
        )

        outcome = coord.execute(bundle, _runtime())

        assert outcome.executed is False
        assert adapter.calls == 0
        assert "backend_memory_insufficient" in (outcome.result.failure_reason or "")
        assert "current=512" in (outcome.result.failure_reason or "")


# ---------------------------------------------------------------------------
# Allow path: dispatch + record events
# ---------------------------------------------------------------------------


class TestAllowPathRecording:
    def test_records_started_finished_execution_outcome_events(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(
            "OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"),
        )
        # Pretend RAM is plenty so the threshold doesn't fire.
        monkeypatch.setattr(
            UsageStore, "available_memory_mb", staticmethod(lambda: 32_000),
        )
        store = UsageStore()
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_AllowPolicy(),
            usage_store=store,
            backend_caps={
                "direct_local": BackendCapSettings(
                    max_per_day=10, max_concurrent=4,
                    min_available_memory_mb=1024,
                ),
            },
        )

        outcome = coord.execute(bundle, _runtime())

        assert outcome.executed is True
        assert adapter.calls == 1
        assert outcome.result.success is True

        events = store.load()["events"]
        kinds = [e.get("kind") for e in events]
        assert "execution_started" in kinds
        assert "execution_finished" in kinds
        assert "execution" in kinds
        assert "execution_outcome" in kinds
        # All four are tagged with backend=direct_local
        for k in ("execution_started", "execution_finished",
                  "execution", "execution_outcome"):
            ev = next(e for e in events if e.get("kind") == k)
            assert ev.get("backend") == "direct_local", f"{k} missing backend"


class TestFinishedFiresOnAdapterCrash:
    def test_finished_marker_fires_when_adapter_crashes(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        """A crashed adapter must NOT deadlock the per-backend max_concurrent cap.

        The recovery loop catches the adapter exception and synthesizes a
        crash result, so the coordinator returns normally — but the
        finally block must still record the finished marker.
        """
        monkeypatch.setenv(
            "OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"),
        )
        store = UsageStore()
        bundle = _bundle()
        adapter = _CrashingAdapter()
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_AllowPolicy(),
            usage_store=store,
            backend_caps={"direct_local": BackendCapSettings(max_concurrent=1)},
        )

        outcome = coord.execute(bundle, _runtime())

        # The recovery loop turned the adapter raise into a crash result.
        assert outcome.result.success is False
        # Started event recorded, finished event also recorded → 0 in flight.
        assert store.concurrent_runs_for_backend(
            "direct_local", now=datetime.now(UTC),
        ) == 0


# ---------------------------------------------------------------------------
# Backwards compat: usage_store=None preserves existing test behavior
# ---------------------------------------------------------------------------


class TestNoUsageStorePreservesBehavior:
    def test_no_usage_store_does_not_record_or_block(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        # Even with rate exhausted on a separate store, a coordinator
        # without usage_store sees no caps — preserves stub-adapter tests.
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_AllowPolicy(),
            # no usage_store, no backend_caps
        )
        outcome = coord.execute(bundle, _runtime())
        assert outcome.executed is True
        assert adapter.calls == 1


class TestNoBackendCapsAllowsButRecords:
    def test_usage_store_without_caps_still_records_events(
        self, monkeypatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setenv(
            "OPERATIONS_CENTER_EXECUTION_USAGE_PATH", str(tmp_path / "usage.json"),
        )
        store = UsageStore()
        bundle = _bundle()
        adapter = _RecordingAdapter(_success(bundle))
        coord = ExecutionCoordinator(
            adapter_registry=_Registry(adapter),
            policy_engine=_AllowPolicy(),
            usage_store=store,
            # no backend_caps — recording happens, enforcement is a no-op
        )
        outcome = coord.execute(bundle, _runtime())
        assert outcome.executed is True
        events = store.load()["events"]
        assert any(e.get("kind") == "execution_started" for e in events)
        assert any(e.get("kind") == "execution_finished" for e in events)
