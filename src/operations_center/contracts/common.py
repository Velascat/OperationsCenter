# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
common.py — shared value objects used across multiple contract models.

These are nested types embedded inside OC internal models such as
``OcPlanningProposal``, ``ExecutionRequest``, and ``ExecutionResult``.
They carry structured domain data that would
otherwise be encoded in untyped dicts or strings.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .enums import ValidationStatus


class TaskTarget(BaseModel):
    """Identifies the repository and branch context for a task."""
    repo_key: str = Field(description="Logical name for the repository (e.g. 'api-service')")
    clone_url: str = Field(description="Git clone URL")
    base_branch: str = Field(description="Branch from which the task branch is created")
    allowed_paths: list[str] = Field(
        default_factory=list,
        description="Glob patterns restricting which files may be modified. Empty = no restriction.",
    )

    model_config = {"frozen": True}


class ExecutionConstraints(BaseModel):
    """Limits and guardrails applied to an execution run."""
    max_changed_files: Optional[int] = Field(
        default=None,
        description="Execution is aborted if more files than this are changed. None = unlimited.",
    )
    timeout_seconds: int = Field(
        default=300,
        ge=1,
        description="Wall-clock timeout for the full execution run.",
    )
    allowed_paths: list[str] = Field(
        default_factory=list,
        description="Subset of TaskTarget.allowed_paths applied at execution time.",
    )
    require_clean_validation: bool = Field(
        default=True,
        description="If True, execution fails unless all validation commands pass.",
    )
    skip_baseline_validation: bool = Field(
        default=False,
        description="Skip the pre-execution baseline validation step.",
    )

    model_config = {"frozen": True}


class ValidationProfile(BaseModel):
    """Specifies which validation commands to run and how."""
    profile_name: str = Field(description="Logical name, e.g. 'strict', 'lint_only', 'off'")
    commands: list[str] = Field(
        default_factory=list,
        description="Shell commands executed to validate the repo state.",
    )
    timeout_seconds: int = Field(default=300, ge=1)
    fail_fast: bool = Field(
        default=False,
        description="Stop running commands on first failure.",
    )

    model_config = {"frozen": True}


class BranchPolicy(BaseModel):
    """Governs how execution branches are named and pushed."""
    branch_prefix: str = Field(
        default="auto/",
        description="Prefix for generated task branches.",
    )
    push_on_success: bool = Field(
        default=True,
        description="Push the task branch to remote when execution succeeds.",
    )
    open_pr: bool = Field(
        default=False,
        description="Open a pull request automatically after a successful push.",
    )
    allowed_base_branches: list[str] = Field(
        default_factory=list,
        description="If set, only these branches may be used as the base. Empty = any.",
    )

    model_config = {"frozen": True}


class ChangedFileRef(BaseModel):
    """A single file that was modified during execution."""
    path: str = Field(description="Repo-relative path to the changed file")
    change_type: str = Field(
        default="modified",
        description="One of: added, modified, deleted, renamed",
    )
    lines_added: Optional[int] = None
    lines_removed: Optional[int] = None

    model_config = {"frozen": True}


class ValidationSummary(BaseModel):
    """Aggregated result of one or more validation commands."""
    status: ValidationStatus
    commands_run: int = Field(default=0, ge=0)
    commands_passed: int = Field(default=0, ge=0)
    commands_failed: int = Field(default=0, ge=0)
    failure_excerpt: Optional[str] = Field(
        default=None,
        description="First failure output, truncated for logging.",
    )
    duration_ms: Optional[int] = Field(default=None, ge=0)

    model_config = {"frozen": True}
