"""Compatibility-only request/result models for the quarantined legacy runtime."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from control_plane.domain.models import BoardTask, RepoTarget, ValidationResult


class LegacyExecutionRequest(BaseModel):
    run_id: str
    task: BoardTask
    repo_target: RepoTarget
    workspace_path: Path
    task_branch: str
    goal_file_path: Path


class LegacyExecutionResult(BaseModel):
    run_id: str
    worker_role: str | None = None
    task_kind: str | None = None
    success: bool
    outcome_status: str = "executed"
    outcome_reason: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    internal_changed_files: list[str] = Field(default_factory=list)
    diff_stat_excerpt: str | None = None
    validation_passed: bool = False
    validation_retried: bool = False
    validation_results: list[ValidationResult] = Field(default_factory=list)
    initial_validation_results: list[ValidationResult] = Field(default_factory=list)
    branch_pushed: bool = False
    draft_branch_pushed: bool = False
    push_reason: str | None = None
    pull_request_url: str | None = None
    execution_stderr_excerpt: str | None = None
    summary: str
    artifacts: list[str] = Field(default_factory=list)
    policy_violations: list[str] = Field(default_factory=list)
    final_status: str | None = None
    blocked_classification: str | None = None
    follow_up_task_ids: list[str] = Field(default_factory=list)
    quality_suppression_counts: dict[str, int] = Field(default_factory=dict)
