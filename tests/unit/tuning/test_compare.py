"""
Unit tests for tuning/compare.py — compare_backends() and compare_by_task_type().

Covers: grouping, rate computation, evidence strength, reliability class,
change evidence class, latency class, sparse samples, unknown metadata.
"""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import ExecutionStatus, FailureReasonCategory, ValidationStatus
from operations_center.tuning.compare import compare_backends, compare_by_task_type
from operations_center.tuning.routing_models import (
    ChangeEvidenceClass,
    EvidenceStrength,
    LatencyClass,
    ReliabilityClass,
)

from .conftest import (
    make_n_failures,
    make_n_successes,
    make_no_changes,
    make_record,
    make_success,
    make_timeout,
    make_unknown_changed_files,
)


class TestEmptyInput:
    def test_empty_records_returns_empty(self):
        assert compare_backends([]) == []

    def test_compare_by_task_type_empty(self):
        assert compare_by_task_type([]) == []


class TestGrouping:
    def test_single_backend_single_group(self):
        records = make_n_successes(5, backend="kodo", lane="claude_cli")
        summaries = compare_backends(records)
        assert len(summaries) == 1
        assert summaries[0].backend == "kodo"
        assert summaries[0].lane == "claude_cli"

    def test_two_backends_two_groups(self):
        records = (
            make_n_successes(5, backend="kodo", lane="claude_cli")
            + make_n_successes(5, backend="archon", lane="claude_cli")
        )
        summaries = compare_backends(records)
        assert len(summaries) == 2
        backends = {s.backend for s in summaries}
        assert backends == {"kodo", "archon"}

    def test_two_lanes_two_groups(self):
        records = (
            make_n_successes(5, backend="kodo", lane="claude_cli")
            + make_n_successes(5, backend="kodo", lane="aider_local")
        )
        summaries = compare_backends(records)
        assert len(summaries) == 2
        lanes = {s.lane for s in summaries}
        assert lanes == {"claude_cli", "aider_local"}

    def test_missing_backend_groups_under_unknown(self):
        _record = make_success()
        record2 = make_record(backend=None, lane="claude_cli")
        summaries = compare_backends([record2])
        assert summaries[0].backend == "unknown"

    def test_sample_size_matches_group_count(self):
        records = make_n_successes(12, backend="kodo", lane="claude_cli")
        summaries = compare_backends(records)
        assert summaries[0].sample_size == 12


class TestSuccessRate:
    def test_all_success_gives_1_0(self):
        records = make_n_successes(10)
        s = compare_backends(records)[0]
        assert s.success_rate == 1.0

    def test_all_failure_gives_0_0(self):
        records = make_n_failures(10)
        s = compare_backends(records)[0]
        assert s.success_rate == 0.0

    def test_mixed_rate(self):
        records = make_n_successes(8) + make_n_failures(2)
        s = compare_backends(records)[0]
        assert s.success_rate == pytest.approx(0.8)
        assert s.failure_rate == pytest.approx(0.2)

    def test_partial_rate_from_no_changes(self):
        records = make_n_successes(8) + [make_no_changes() for _ in range(2)]
        s = compare_backends(records)[0]
        assert s.partial_rate == pytest.approx(0.2)


class TestTimeoutRate:
    def test_timeout_rate(self):
        records = make_n_successes(8) + [make_timeout() for _ in range(2)]
        s = compare_backends(records)[0]
        assert s.timeout_rate == pytest.approx(0.2)
        assert s.failure_rate == pytest.approx(0.2)  # timeout counts as failure

    def test_no_timeouts_gives_zero(self):
        records = make_n_successes(5)
        s = compare_backends(records)[0]
        assert s.timeout_rate == 0.0


class TestValidationRates:
    def test_all_skipped_gives_zero_pass_rate(self):
        records = make_n_successes(5)  # default validation=skipped
        s = compare_backends(records)[0]
        assert s.validation_pass_rate == 0.0
        assert s.validation_skip_rate == 1.0

    def test_validation_passed(self):
        records = [
            make_record(validation_status=ValidationStatus.PASSED),
            make_record(validation_status=ValidationStatus.PASSED, run_id="run-0002"),
            make_record(validation_status=ValidationStatus.SKIPPED, run_id="run-0003"),
        ]
        s = compare_backends(records)[0]
        assert s.validation_pass_rate == pytest.approx(2 / 3, abs=0.005)


class TestEvidenceStrength:
    def test_single_record_is_weak(self):
        records = [make_success()]
        s = compare_backends(records)[0]
        assert s.evidence_strength == EvidenceStrength.WEAK

    def test_eight_records_is_moderate(self):
        records = make_n_successes(8)
        s = compare_backends(records)[0]
        assert s.evidence_strength == EvidenceStrength.MODERATE

    def test_twenty_records_is_strong(self):
        records = make_n_successes(20)
        s = compare_backends(records)[0]
        assert s.evidence_strength == EvidenceStrength.STRONG

    def test_seven_is_still_weak(self):
        records = make_n_successes(7)
        s = compare_backends(records)[0]
        assert s.evidence_strength == EvidenceStrength.WEAK

    def test_nineteen_is_moderate(self):
        records = make_n_successes(19)
        s = compare_backends(records)[0]
        assert s.evidence_strength == EvidenceStrength.MODERATE


class TestReliabilityClass:
    def test_high_reliability(self):
        records = make_n_successes(20)
        s = compare_backends(records)[0]
        assert s.reliability_class == ReliabilityClass.HIGH

    def test_low_reliability(self):
        # 50% success rate → LOW
        records = make_n_successes(5) + make_n_failures(5)
        s = compare_backends(records)[0]
        assert s.reliability_class == ReliabilityClass.LOW

    def test_medium_reliability(self):
        # 7/10 = 0.7 → MEDIUM
        records = make_n_successes(7) + make_n_failures(3)
        s = compare_backends(records)[0]
        assert s.reliability_class == ReliabilityClass.MEDIUM

    def test_exactly_85_pct_is_high(self):
        records = make_n_successes(17) + make_n_failures(3)
        s = compare_backends(records)[0]
        assert s.reliability_class == ReliabilityClass.HIGH


class TestChangeEvidenceClass:
    def test_all_known_files_is_strong(self):
        records = make_n_successes(10)  # success → known files
        s = compare_backends(records)[0]
        assert s.change_evidence_class == ChangeEvidenceClass.STRONG

    def test_all_unknown_is_poor(self):
        records = [make_unknown_changed_files() for _ in range(10)]
        s = compare_backends(records)[0]
        assert s.change_evidence_class == ChangeEvidenceClass.POOR

    def test_mixed_known_unknown_partial(self):
        # 5 known, 5 unknown → 50% → PARTIAL (< 80%)
        records = (
            make_n_successes(5)
            + [make_unknown_changed_files(run_id=f"run-u-{i:04d}") for i in range(5)]
        )
        s = compare_backends(records)[0]
        assert s.change_evidence_class == ChangeEvidenceClass.PARTIAL

    def test_not_applicable_excluded_from_rate(self):
        # All policy_blocked → NOT_APPLICABLE → UNKNOWN class
        records = [
            make_record(
                status=ExecutionStatus.FAILED,
                success=False,
                failure_category=FailureReasonCategory.POLICY_BLOCKED,
                changed_files=[],
                run_id=f"run-pb-{i:04d}",
            )
            for i in range(5)
        ]
        s = compare_backends(records)[0]
        assert s.change_evidence_class == ChangeEvidenceClass.UNKNOWN


class TestLatencyClass:
    def test_unknown_when_no_metadata(self):
        records = make_n_successes(5)
        s = compare_backends(records)[0]
        assert s.latency_class == LatencyClass.UNKNOWN
        assert s.median_duration_ms is None

    def test_fast_when_below_30s(self):
        records = [make_success(duration_ms=10_000, run_id=f"r-{i}") for i in range(5)]
        s = compare_backends(records)[0]
        assert s.latency_class == LatencyClass.FAST
        assert s.median_duration_ms == 10_000

    def test_slow_when_above_120s(self):
        records = [make_success(duration_ms=200_000, run_id=f"r-{i}") for i in range(5)]
        s = compare_backends(records)[0]
        assert s.latency_class == LatencyClass.SLOW

    def test_medium_at_60s(self):
        records = [make_success(duration_ms=60_000, run_id=f"r-{i}") for i in range(5)]
        s = compare_backends(records)[0]
        assert s.latency_class == LatencyClass.MEDIUM

    def test_partial_duration_metadata(self):
        # Mixed: some have duration, some don't
        r1 = make_success(duration_ms=20_000, run_id="r-0")
        r2 = make_success(run_id="r-1")  # no duration
        s = compare_backends([r1, r2])[0]
        assert s.latency_class == LatencyClass.FAST  # median of [20000]


class TestFilterByTaskType:
    def test_filter_by_task_type_scope(self):
        records = (
            make_n_successes(5, task_type="bug_fix")
            + [make_success(task_type="feature", run_id=f"feat-{i:04d}") for i in range(3)]
        )
        # This relies on task_type being in metadata; filter in compare_backends
        summaries = compare_backends(records, task_type_scope=["bug_fix"])
        assert len(summaries) == 1
        assert summaries[0].sample_size == 5

    def test_no_filter_returns_all(self):
        records = make_n_successes(5, task_type="bug_fix") + make_n_successes(3, task_type="feature")
        summaries = compare_backends(records)
        assert summaries[0].sample_size == 8


class TestCompareByTaskType:
    def test_splits_by_task_type(self):
        records = (
            make_n_successes(5, task_type="bug_fix")
            + make_n_successes(3, task_type="feature")
        )
        summaries = compare_by_task_type(records)
        task_scopes = [s.task_type_scope for s in summaries]
        assert ["bug_fix"] in task_scopes
        assert ["feature"] in task_scopes

    def test_unknown_task_type_groups_correctly(self):
        records = make_n_successes(4)  # no task_type metadata
        summaries = compare_by_task_type(records)
        assert summaries[0].task_type_scope == ["unknown"]
