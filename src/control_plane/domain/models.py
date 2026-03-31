from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


ExecutionMode = Literal["goal"]


class ParsedTaskBody(BaseModel):
    execution_metadata: dict[str, object]
    goal_text: str
    constraints_text: str | None = None


class BoardTask(BaseModel):
    task_id: str
    project_id: str
    title: str
    description: str | None = None
    status: str
    labels: list[str] = Field(default_factory=list)
    repo_key: str
    base_branch: str
    execution_mode: ExecutionMode
    allowed_paths: list[str] = Field(default_factory=list)
    validation_profile: str | None = None
    open_pr: bool = False
    goal_text: str
    constraints_text: str | None = None


class RepoTarget(BaseModel):
    repo_key: str
    clone_url: str
    default_branch: str
    workdir_name: str
    validation_commands: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    allowed_base_branches: list[str] = Field(default_factory=list)


class ExecutionRequest(BaseModel):
    run_id: str
    task: BoardTask
    repo_target: RepoTarget
    workspace_path: Path
    task_branch: str
    goal_file_path: Path


class ValidationResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class ExecutionResult(BaseModel):
    run_id: str
    worker_role: str | None = None
    task_kind: str | None = None
    success: bool
    outcome_status: str = "executed"
    outcome_reason: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    diff_stat_excerpt: str | None = None
    validation_passed: bool = False
    validation_results: list[ValidationResult] = Field(default_factory=list)
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
