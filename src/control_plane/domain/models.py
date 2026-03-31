from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


ExecutionMode = Literal["goal", "test", "improve"]


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


class RepoTarget(BaseModel):
    repo_key: str
    clone_url: str
    default_branch: str
    workdir_name: str
    validation_commands: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    allowed_base_branches: list[str] = Field(default_factory=list)


class ExecutionRequest(BaseModel):
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
    success: bool
    changed_files: list[str] = Field(default_factory=list)
    validation_passed: bool = False
    validation_results: list[ValidationResult] = Field(default_factory=list)
    branch_pushed: bool = False
    pull_request_url: str | None = None
    summary: str
    artifacts: list[str] = Field(default_factory=list)
