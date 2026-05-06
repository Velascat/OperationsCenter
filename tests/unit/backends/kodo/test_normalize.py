# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for kodo result normalization: KodoRunCapture → ExecutionResult."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


from operations_center.backends.kodo.models import KodoArtifactCapture, KodoRunCapture
from operations_center.backends.kodo.normalize import normalize
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _capture(
    exit_code: int = 0,
    stdout: str = "kodo ran successfully",
    stderr: str = "",
    run_id: str = "run-1",
    timeout_hit: bool = False,
    artifacts=None,
) -> KodoRunCapture:
    started = _now()
    return KodoRunCapture(
        run_id=run_id,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        command=["kodo", "--goal-file", "goal.md"],
        started_at=started,
        finished_at=started,
        duration_ms=1000,
        timeout_hit=timeout_hit,
        artifacts=artifacts or [],
    )


def _normalize(capture: KodoRunCapture, **kw):
    defaults = dict(proposal_id="prop-1", decision_id="dec-1")
    defaults.update(kw)
    return normalize(capture, **defaults)


# ---------------------------------------------------------------------------
# Success normalization
# ---------------------------------------------------------------------------

class TestSuccessNormalization:
    def test_success_result_fields(self):
        result = _normalize(_capture(exit_code=0))
        assert result.success is True
        assert result.status == ExecutionStatus.SUCCEEDED
        assert result.failure_category is None
        assert result.failure_reason is None

    def test_run_id_preserved(self):
        result = _normalize(_capture(run_id="run-xyz"))
        assert result.run_id == "run-xyz"

    def test_proposal_and_decision_ids_preserved(self):
        result = _normalize(_capture(), proposal_id="p-1", decision_id="d-1")
        assert result.proposal_id == "p-1"
        assert result.decision_id == "d-1"

    def test_branch_name_set(self):
        result = _normalize(_capture(), branch_name="auto/lint-fix-abc")
        assert result.branch_name == "auto/lint-fix-abc"

    def test_branch_pushed_is_false(self):
        # branch push is a lane-runner concern, never set by the adapter
        result = _normalize(_capture())
        assert result.branch_pushed is False

    def test_validation_skipped_when_not_ran(self):
        result = _normalize(_capture())
        assert result.validation.status == ValidationStatus.SKIPPED

    def test_validation_passed(self):
        result = _normalize(_capture(), validation_ran=True, validation_passed=True, validation_duration_ms=500)
        assert result.validation.status == ValidationStatus.PASSED
        assert result.validation.commands_passed == 1
        assert result.validation.duration_ms == 500

    def test_validation_failed(self):
        result = _normalize(
            _capture(),
            validation_ran=True,
            validation_passed=False,
            validation_excerpt="AssertionError: 1 != 2",
        )
        assert result.validation.status == ValidationStatus.FAILED
        assert result.validation.commands_failed == 1
        assert result.validation.failure_excerpt == "AssertionError: 1 != 2"


# ---------------------------------------------------------------------------
# Failure normalization
# ---------------------------------------------------------------------------

class TestFailureNormalization:
    def test_nonzero_exit_is_failure(self):
        result = _normalize(_capture(exit_code=1, stderr="something broke"))
        assert result.success is False
        assert result.status == ExecutionStatus.FAILED

    def test_timeout_status(self):
        capture = _capture(exit_code=-1, stderr="[timeout: process group killed after 300s]", timeout_hit=True)
        result = _normalize(capture)
        assert result.status == ExecutionStatus.TIMED_OUT
        assert result.failure_category == FailureReasonCategory.TIMEOUT

    def test_failure_reason_populated(self):
        result = _normalize(_capture(exit_code=1, stderr="ruff: 12 errors found"))
        assert result.failure_reason is not None
        assert len(result.failure_reason) > 0

    def test_failure_category_backend_error_for_generic_failure(self):
        result = _normalize(_capture(exit_code=1, stderr="unknown error"))
        assert result.failure_category == FailureReasonCategory.BACKEND_ERROR

    def test_no_changes_category_on_failure_exit(self):
        # exit 1 with "no changes" content maps to NO_CHANGES category
        result = _normalize(_capture(exit_code=1, stdout="nothing to commit, working tree clean"))
        assert result.failure_category == FailureReasonCategory.NO_CHANGES

    def test_conflict_category(self):
        result = _normalize(_capture(exit_code=1, stderr="Auto-merge failed; fix conflicts"))
        assert result.failure_category == FailureReasonCategory.CONFLICT


# ---------------------------------------------------------------------------
# Artifact normalization
# ---------------------------------------------------------------------------

class TestArtifactNormalization:
    def test_log_excerpt_artifact_preserved(self):
        cap = _capture(artifacts=[
            KodoArtifactCapture(label="kodo run log", content="log content", artifact_type="log_excerpt"),
        ])
        result = _normalize(cap)
        assert len(result.artifacts) == 1
        assert result.artifacts[0].artifact_type == ArtifactType.LOG_EXCERPT
        assert result.artifacts[0].label == "kodo run log"
        assert result.artifacts[0].content == "log content"

    def test_unknown_artifact_type_falls_back_to_log_excerpt(self):
        cap = _capture(artifacts=[
            KodoArtifactCapture(label="mystery", content="data", artifact_type="nonexistent_type"),
        ])
        result = _normalize(cap)
        assert result.artifacts[0].artifact_type == ArtifactType.LOG_EXCERPT

    def test_diff_artifact_preserved(self):
        cap = _capture(artifacts=[
            KodoArtifactCapture(label="diff", content="--- a/f\n+++ b/f", artifact_type="diff"),
        ])
        result = _normalize(cap)
        assert result.artifacts[0].artifact_type == ArtifactType.DIFF

    def test_no_artifacts_is_empty_list(self):
        result = _normalize(_capture())
        assert isinstance(result.artifacts, list)


# ---------------------------------------------------------------------------
# Changed files (workspace discovery)
# ---------------------------------------------------------------------------

class TestChangedFiles:
    def test_no_workspace_returns_empty_changed_files(self):
        result = _normalize(_capture())
        assert result.changed_files == []
        assert result.changed_files_source == "unknown"
        assert result.changed_files_confidence == 0.0

    def test_nonexistent_workspace_returns_empty(self):
        result = _normalize(_capture(), workspace_path=Path("/tmp/nonexistent-workspace-xyz"))
        assert result.changed_files == []
        assert result.changed_files_source == "unknown"

    def test_diff_stat_none_when_no_changed_files(self):
        result = _normalize(_capture())
        assert result.diff_stat_excerpt is None


# ---------------------------------------------------------------------------
# ExecutionResult is serialisable
# ---------------------------------------------------------------------------

class TestResultSerialisation:
    def test_json_round_trip(self):
        from operations_center.contracts.execution import ExecutionResult
        result = _normalize(_capture())
        restored = ExecutionResult.model_validate_json(result.model_dump_json())
        assert restored == result

    def test_status_enum_serialises_as_string(self):
        import json
        result = _normalize(_capture())
        parsed = json.loads(result.model_dump_json())
        assert parsed["status"] == "succeeded"
