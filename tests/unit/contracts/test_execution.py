"""Tests for ExecutionRequest, ExecutionArtifact, RunTelemetry, ExecutionResult."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.contracts.execution import (
    ExecutionArtifact,
    ExecutionRequest,
    ExecutionResult,
    RunTelemetry,
)
from operations_center.contracts.common import ChangedFileRef, ValidationSummary
from operations_center.contracts.enums import (
    ArtifactType,
    ExecutionStatus,
    FailureReasonCategory,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_request(**kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-1",
        decision_id="dec-1",
        goal_text="Fix lint errors",
        repo_key="svc",
        clone_url="https://git.example.com/svc.git",
        base_branch="main",
        task_branch="auto/fix-lint-abc123",
        workspace_path=Path("/tmp/ws/svc"),
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


def _minimal_result(**kw) -> ExecutionResult:
    defaults = dict(
        run_id="run-1",
        proposal_id="prop-1",
        decision_id="dec-1",
        status=ExecutionStatus.SUCCEEDED,
        success=True,
    )
    defaults.update(kw)
    return ExecutionResult(**defaults)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# ExecutionRequest
# ---------------------------------------------------------------------------

class TestExecutionRequest:
    def test_minimal(self):
        r = _minimal_request()
        assert r.proposal_id == "prop-1"
        assert r.task_branch == "auto/fix-lint-abc123"
        assert r.workspace_path == Path("/tmp/ws/svc")

    def test_auto_run_id(self):
        r1 = _minimal_request()
        r2 = _minimal_request()
        assert r1.run_id != r2.run_id

    def test_defaults(self):
        r = _minimal_request()
        assert r.allowed_paths == []
        assert r.max_changed_files is None
        assert r.timeout_seconds == 300
        assert r.require_clean_validation is True
        assert r.validation_commands == []
        assert r.goal_file_path is None
        assert r.constraints_text is None

    def test_frozen(self):
        r = _minimal_request()
        with pytest.raises(Exception):
            r.goal_text = "other"  # type: ignore[misc]

    def test_json_round_trip(self):
        r = _minimal_request()
        restored = ExecutionRequest.model_validate_json(r.model_dump_json())
        assert restored == r

    def test_path_serialised_as_string(self):
        r = _minimal_request(workspace_path=Path("/home/dev/ws"))
        parsed = json.loads(r.model_dump_json())
        assert parsed["workspace_path"] == "/home/dev/ws"

    def test_with_optional_fields(self):
        r = _minimal_request(
            constraints_text="Do not touch auth/",
            allowed_paths=["src/**"],
            max_changed_files=5,
            validation_commands=["ruff check .", "pytest"],
            goal_file_path=Path("/tmp/ws/svc/.goal.md"),
        )
        assert r.max_changed_files == 5
        assert len(r.validation_commands) == 2
        assert r.goal_file_path == Path("/tmp/ws/svc/.goal.md")

    def test_timeout_must_be_positive(self):
        with pytest.raises(Exception):
            _minimal_request(timeout_seconds=0)


# ---------------------------------------------------------------------------
# ExecutionArtifact
# ---------------------------------------------------------------------------

class TestExecutionArtifact:
    def test_minimal(self):
        a = ExecutionArtifact(artifact_type=ArtifactType.DIFF, label="final diff")
        assert a.artifact_type == ArtifactType.DIFF
        assert a.content is None
        assert a.uri is None

    def test_auto_artifact_id(self):
        a1 = ExecutionArtifact(artifact_type=ArtifactType.LOG_EXCERPT, label="l")
        a2 = ExecutionArtifact(artifact_type=ArtifactType.LOG_EXCERPT, label="l")
        assert a1.artifact_id != a2.artifact_id

    def test_with_inline_content(self):
        a = ExecutionArtifact(
            artifact_type=ArtifactType.DIFF,
            label="patch",
            content="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x=1\n+x=2\n",
            size_bytes=50,
        )
        assert a.content is not None
        assert a.size_bytes == 50

    def test_with_uri(self):
        a = ExecutionArtifact(
            artifact_type=ArtifactType.VALIDATION_REPORT,
            label="report",
            uri="s3://bucket/reports/run-1.json",
        )
        assert a.uri == "s3://bucket/reports/run-1.json"

    def test_frozen(self):
        a = ExecutionArtifact(artifact_type=ArtifactType.DIFF, label="d")
        with pytest.raises(Exception):
            a.label = "other"  # type: ignore[misc]

    def test_json_round_trip(self):
        a = ExecutionArtifact(artifact_type=ArtifactType.BRANCH_REF, label="branch", content="auto/fix")
        restored = ExecutionArtifact.model_validate_json(a.model_dump_json())
        assert restored == a


# ---------------------------------------------------------------------------
# RunTelemetry
# ---------------------------------------------------------------------------

class TestRunTelemetry:
    def test_minimal(self):
        now = _now()
        t = RunTelemetry(
            run_id="run-1",
            started_at=now,
            finished_at=now,
            duration_ms=100,
        )
        assert t.run_id == "run-1"
        assert t.llm_calls == 0
        assert t.labels == {}

    def test_with_counts(self):
        now = _now()
        t = RunTelemetry(
            run_id="run-2",
            started_at=now,
            finished_at=now,
            duration_ms=4200,
            llm_calls=12,
            llm_input_tokens=8000,
            llm_output_tokens=1500,
            tool_calls=30,
            lane_name="aider_local",
            backend_name="kodo",
            backend_version="0.3.1",
        )
        assert t.llm_calls == 12
        assert t.lane_name == "aider_local"

    def test_labels_dict(self):
        now = _now()
        t = RunTelemetry(
            run_id="r",
            started_at=now,
            finished_at=now,
            duration_ms=0,
            labels={"experiment": "trial-A"},
        )
        assert t.labels["experiment"] == "trial-A"

    def test_negative_duration_raises(self):
        now = _now()
        with pytest.raises(Exception):
            RunTelemetry(run_id="r", started_at=now, finished_at=now, duration_ms=-1)

    def test_json_round_trip(self):
        now = _now()
        t = RunTelemetry(run_id="r", started_at=now, finished_at=now, duration_ms=99)
        restored = RunTelemetry.model_validate_json(t.model_dump_json())
        assert restored == t


# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------

class TestExecutionResult:
    def test_minimal_success(self):
        r = _minimal_result()
        assert r.success is True
        assert r.status == ExecutionStatus.SUCCEEDED
        assert r.changed_files == []
        assert r.artifacts == []
        assert r.branch_pushed is False
        assert r.failure_category is None

    def test_minimal_failure(self):
        r = _minimal_result(
            status=ExecutionStatus.FAILED,
            success=False,
            failure_category=FailureReasonCategory.VALIDATION_FAILED,
            failure_reason="pytest: 3 failures",
        )
        assert r.success is False
        assert r.failure_category == FailureReasonCategory.VALIDATION_FAILED

    def test_with_changed_files(self):
        r = _minimal_result(
            changed_files=[
                ChangedFileRef(path="src/main.py", lines_added=10, lines_removed=3),
                ChangedFileRef(path="src/utils.py", change_type="added", lines_added=50),
            ],
            changed_files_source="git_diff",
            changed_files_confidence=1.0,
        )
        assert len(r.changed_files) == 2
        assert r.changed_files[0].lines_added == 10
        assert r.changed_files_source == "git_diff"
        assert r.changed_files_confidence == 1.0

    def test_with_validation_summary(self):
        r = _minimal_result(
            validation=ValidationSummary(
                status=ValidationStatus.PASSED,
                commands_run=2,
                commands_passed=2,
            )
        )
        assert r.validation.status == ValidationStatus.PASSED

    def test_with_artifacts(self):
        r = _minimal_result(
            artifacts=[
                ExecutionArtifact(artifact_type=ArtifactType.DIFF, label="diff"),
                ExecutionArtifact(artifact_type=ArtifactType.PR_URL, label="pr", content="https://github.com/org/repo/pull/42"),
            ]
        )
        assert len(r.artifacts) == 2

    def test_branch_push_fields(self):
        r = _minimal_result(
            branch_pushed=True,
            branch_name="auto/fix-lint-abc123",
            pull_request_url="https://github.com/org/repo/pull/42",
        )
        assert r.branch_pushed is True
        assert r.pull_request_url == "https://github.com/org/repo/pull/42"

    def test_frozen(self):
        r = _minimal_result()
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]

    def test_json_round_trip(self):
        r = _minimal_result()
        restored = ExecutionResult.model_validate_json(r.model_dump_json())
        assert restored == r

    def test_json_enum_values_are_strings(self):
        r = _minimal_result()
        parsed = json.loads(r.model_dump_json())
        assert parsed["status"] == "succeeded"

    def test_failed_result_round_trip(self):
        r = _minimal_result(
            status=ExecutionStatus.FAILED,
            success=False,
            failure_category=FailureReasonCategory.TIMEOUT,
            failure_reason="timed out after 300s",
        )
        restored = ExecutionResult.model_validate_json(r.model_dump_json())
        assert restored.failure_category == FailureReasonCategory.TIMEOUT


# ---------------------------------------------------------------------------
# Cross-model integration
# ---------------------------------------------------------------------------

class TestContractIntegration:
    """Verify that models that reference each other serialise cleanly end-to-end."""

    def test_result_with_all_nested_types(self):
        r = ExecutionResult(
            run_id="run-99",
            proposal_id="prop-1",
            decision_id="dec-1",
            status=ExecutionStatus.SUCCEEDED,
            success=True,
            changed_files=[ChangedFileRef(path="src/x.py", lines_added=5)],
            changed_files_source="git_diff",
            changed_files_confidence=1.0,
            diff_stat_excerpt="1 file changed, 5 insertions(+)",
            validation=ValidationSummary(
                status=ValidationStatus.PASSED,
                commands_run=1,
                commands_passed=1,
                duration_ms=840,
            ),
            branch_pushed=True,
            branch_name="auto/fix-abc",
            pull_request_url="https://github.com/org/repo/pull/7",
            artifacts=[
                ExecutionArtifact(
                    artifact_type=ArtifactType.DIFF,
                    label="final diff",
                    content="+x = 2",
                ),
            ],
        )

        restored = ExecutionResult.model_validate_json(r.model_dump_json())
        assert restored == r
        assert restored.artifacts[0].artifact_type == ArtifactType.DIFF
        assert restored.validation.duration_ms == 840
