# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 6 dispatch request and result models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class DispatchStatus(str, Enum):
    """Canonical status of a dispatch attempt, aligned with Phase 2 RunStatus vocabulary."""

    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class FailureKind(str, Enum):
    """Distinguishes the failure mode when status != completed.

    Lets callers tell apart process failures from contract discovery failures
    without parsing error strings.
    """

    PROCESS_NONZERO_EXIT = "process_nonzero_exit"
    PROCESS_TIMEOUT = "process_timeout"
    EXECUTOR_ERROR = "executor_error"
    RUN_STATUS_MISSING = "run_status_missing"
    RUN_STATUS_INVALID = "run_status_invalid"
    MANIFEST_PATH_MISSING = "manifest_path_missing"
    MANIFEST_PATH_UNRESOLVABLE = "manifest_path_unresolvable"
    UNKNOWN = "unknown"


class ManagedAuditDispatchRequest(BaseModel, frozen=True):
    """Request shape for dispatching a single managed audit run.

    Callers supply repo_id and audit_type. Phase 6 prepares the full
    invocation (identity, command, env) internally using Phase 1–4 contracts.
    """

    repo_id: str = Field(description="Managed repo identifier (e.g. 'videofoundry')")
    audit_type: str = Field(description="Audit type declared in the repo config")
    metadata: dict[str, Any] = Field(default_factory=dict)
    allow_unverified_command: bool = Field(
        default=False,
        description=(
            "Allow audit types with command_status='not_yet_run'. "
            "Verified commands are always allowed regardless of this flag."
        ),
    )
    timeout_seconds: float | None = Field(
        default=None,
        description="Hard wall-clock timeout in seconds. None = no timeout.",
    )
    requested_by: str | None = Field(
        default=None,
        description="Caller identity for audit trails (e.g. 'opsenter-scheduler').",
    )
    correlation_id: str | None = Field(
        default=None,
        description="Caller-supplied correlation ID for tracing dispatch chains.",
    )
    base_env: dict[str, str] | None = Field(
        default=None,
        description=(
            "Base subprocess environment. When None, os.environ is used. "
            "AUDIT_RUN_ID is always injected on top of whatever base is provided."
        ),
    )
    cwd_override: str | None = Field(
        default=None,
        description=(
            "Absolute working directory for the subprocess. "
            "Overrides the value resolved from managed repo config."
        ),
    )

    @field_validator("repo_id")
    @classmethod
    def _repo_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("repo_id must not be empty")
        return v

    @field_validator("audit_type")
    @classmethod
    def _audit_type_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("audit_type must not be empty")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("timeout_seconds must be positive")
        return v


class ManagedAuditDispatchResult(BaseModel, frozen=True):
    """Canonical result of a managed audit dispatch.

    Always returned by dispatch_managed_audit() — process failures and
    contract discovery failures are represented as structured results
    rather than raised exceptions.
    """

    repo_id: str
    audit_type: str
    run_id: str | None = Field(
        description="Run identity string, or None if identity generation failed.",
    )
    status: DispatchStatus
    failure_kind: FailureKind | None = Field(
        default=None,
        description="Specific failure mode when status != completed.",
    )
    process_exit_code: int | None = Field(
        default=None,
        description="Subprocess exit code. None if the process did not start.",
    )
    started_at: datetime
    ended_at: datetime
    duration_seconds: float
    run_status_path: str | None = Field(
        default=None,
        description="Absolute path to run_status.json if located after process exit.",
    )
    artifact_manifest_path: str | None = Field(
        default=None,
        description=(
            "Resolved absolute path to artifact_manifest.json, or None. "
            "Populated on success; also populated on some failures if the "
            "producer wrote contract files despite a nonzero exit."
        ),
    )
    stdout_path: str | None = Field(
        default=None,
        description="Path to the captured stdout log file.",
    )
    stderr_path: str | None = Field(
        default=None,
        description="Path to the captured stderr log file.",
    )
    error: str | None = Field(
        default=None,
        description="Human-readable error summary for failed results.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == DispatchStatus.COMPLETED

    @property
    def has_manifest(self) -> bool:
        return self.artifact_manifest_path is not None
