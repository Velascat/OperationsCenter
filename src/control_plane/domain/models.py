from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ExecutionMode = Literal["goal", "fix_pr", "test_campaign", "improve_campaign"]


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
    validation_timeout_seconds: int = 300
    # When True, baseline validation is skipped entirely.  Use for repos that
    # have pre-existing widespread violations that are not the fault of any
    # single task — prevents an endless fix-validation task cycle.
    skip_baseline_validation: bool = False


class ValidationResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
