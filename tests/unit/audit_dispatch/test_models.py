# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for Phase 6 dispatch request and result models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from operations_center.audit_dispatch.models import (
    DispatchStatus,
    FailureKind,
    ManagedAuditDispatchRequest,
    ManagedAuditDispatchResult,
)

_NOW = datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# ManagedAuditDispatchRequest — validation
# ---------------------------------------------------------------------------


class TestDispatchRequestValidation:
    def test_minimal_request_is_valid(self) -> None:
        req = ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="audit_type_1")
        assert req.repo_id == "example_managed_repo"
        assert req.audit_type == "audit_type_1"

    def test_empty_repo_id_raises(self) -> None:
        with pytest.raises(ValidationError, match="repo_id"):
            ManagedAuditDispatchRequest(repo_id="", audit_type="audit_type_1")

    def test_whitespace_repo_id_raises(self) -> None:
        with pytest.raises(ValidationError, match="repo_id"):
            ManagedAuditDispatchRequest(repo_id="   ", audit_type="audit_type_1")

    def test_empty_audit_type_raises(self) -> None:
        with pytest.raises(ValidationError, match="audit_type"):
            ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="")

    def test_timeout_zero_raises(self) -> None:
        with pytest.raises(ValidationError, match="timeout"):
            ManagedAuditDispatchRequest(
                repo_id="example_managed_repo", audit_type="audit_type_1", timeout_seconds=0
            )

    def test_timeout_negative_raises(self) -> None:
        with pytest.raises(ValidationError, match="timeout"):
            ManagedAuditDispatchRequest(
                repo_id="example_managed_repo", audit_type="audit_type_1", timeout_seconds=-1
            )

    def test_timeout_positive_is_valid(self) -> None:
        req = ManagedAuditDispatchRequest(
            repo_id="example_managed_repo", audit_type="audit_type_1", timeout_seconds=300.0
        )
        assert req.timeout_seconds == 300.0

    def test_timeout_none_is_valid(self) -> None:
        req = ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="audit_type_1")
        assert req.timeout_seconds is None

    def test_default_metadata_is_empty_dict(self) -> None:
        req = ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="audit_type_1")
        assert req.metadata == {}

    def test_allow_unverified_default_is_false(self) -> None:
        req = ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="audit_type_1")
        assert req.allow_unverified_command is False

    def test_base_env_default_is_none(self) -> None:
        req = ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="audit_type_1")
        assert req.base_env is None

    def test_cwd_override_default_is_none(self) -> None:
        req = ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="audit_type_1")
        assert req.cwd_override is None

    def test_correlation_id_optional(self) -> None:
        req = ManagedAuditDispatchRequest(
            repo_id="example_managed_repo",
            audit_type="audit_type_1",
            correlation_id="test-corr-001",
        )
        assert req.correlation_id == "test-corr-001"

    def test_requested_by_optional(self) -> None:
        req = ManagedAuditDispatchRequest(
            repo_id="example_managed_repo",
            audit_type="audit_type_1",
            requested_by="scheduler",
        )
        assert req.requested_by == "scheduler"

    def test_request_is_frozen(self) -> None:
        req = ManagedAuditDispatchRequest(repo_id="example_managed_repo", audit_type="audit_type_1")
        with pytest.raises(Exception):
            req.repo_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ManagedAuditDispatchResult — properties
# ---------------------------------------------------------------------------


def _make_result(**overrides) -> ManagedAuditDispatchResult:
    base: dict = {
        "repo_id": "example_managed_repo",
        "audit_type": "audit_type_1",
        "run_id": "example_managed_repo_audit_type_1_20260426T120000Z_aabb1122",
        "status": DispatchStatus.COMPLETED,
        "process_exit_code": 0,
        "started_at": _NOW,
        "ended_at": _NOW,
        "duration_seconds": 42.0,
    }
    base.update(overrides)
    return ManagedAuditDispatchResult(**base)


class TestDispatchResultProperties:
    def test_succeeded_true_when_completed(self) -> None:
        result = _make_result(status=DispatchStatus.COMPLETED)
        assert result.succeeded is True

    def test_succeeded_false_when_failed(self) -> None:
        result = _make_result(status=DispatchStatus.FAILED)
        assert result.succeeded is False

    def test_succeeded_false_when_interrupted(self) -> None:
        result = _make_result(status=DispatchStatus.INTERRUPTED)
        assert result.succeeded is False

    def test_has_manifest_true_when_path_set(self) -> None:
        result = _make_result(artifact_manifest_path="/some/path/artifact_manifest.json")
        assert result.has_manifest is True

    def test_has_manifest_false_when_none(self) -> None:
        result = _make_result(artifact_manifest_path=None)
        assert result.has_manifest is False

    def test_result_is_frozen(self) -> None:
        result = _make_result()
        with pytest.raises(Exception):
            result.status = DispatchStatus.FAILED  # type: ignore[misc]

    def test_default_metadata_is_empty(self) -> None:
        result = _make_result()
        assert result.metadata == {}


# ---------------------------------------------------------------------------
# DispatchStatus and FailureKind values
# ---------------------------------------------------------------------------


class TestDispatchStatusEnum:
    def test_completed_value(self) -> None:
        assert DispatchStatus.COMPLETED == "completed"

    def test_failed_value(self) -> None:
        assert DispatchStatus.FAILED == "failed"

    def test_interrupted_value(self) -> None:
        assert DispatchStatus.INTERRUPTED == "interrupted"

    def test_unknown_value(self) -> None:
        assert DispatchStatus.UNKNOWN == "unknown"


class TestFailureKindEnum:
    def test_process_nonzero_exit(self) -> None:
        assert FailureKind.PROCESS_NONZERO_EXIT == "process_nonzero_exit"

    def test_process_timeout(self) -> None:
        assert FailureKind.PROCESS_TIMEOUT == "process_timeout"

    def test_run_status_missing(self) -> None:
        assert FailureKind.RUN_STATUS_MISSING == "run_status_missing"

    def test_run_status_invalid(self) -> None:
        assert FailureKind.RUN_STATUS_INVALID == "run_status_invalid"

    def test_manifest_path_missing(self) -> None:
        assert FailureKind.MANIFEST_PATH_MISSING == "manifest_path_missing"
