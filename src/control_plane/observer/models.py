from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field


OBSERVER_VERSION = 1


class RepoContextSnapshot(BaseModel):
    name: str
    path: Path
    current_branch: str
    base_branch: str | None = None
    is_dirty: bool


class CommitMetadata(BaseModel):
    sha_short: str
    author: str
    timestamp: datetime
    subject: str


class FileHotspot(BaseModel):
    path: str
    touch_count: int


class TestSignal(BaseModel):
    status: str
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None


class DependencyDriftSignal(BaseModel):
    status: str
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None


class TodoFileCount(BaseModel):
    path: str
    count: int


class TodoSignal(BaseModel):
    todo_count: int = 0
    fixme_count: int = 0
    top_files: list[TodoFileCount] = Field(default_factory=list)


class ExecutionRunRecord(BaseModel):
    run_id: str
    task_id: str
    worker_role: str
    outcome_status: str  # executed, no_op, skipped, or other
    outcome_reason: str | None = None
    validation_passed: bool | None = None


class ExecutionHealthSignal(BaseModel):
    total_runs: int = 0
    executed_count: int = 0
    no_op_count: int = 0
    validation_failed_count: int = 0
    recent_runs: list[ExecutionRunRecord] = Field(default_factory=list)


class BacklogItem(BaseModel):
    title: str
    item_type: str  # maintenance, feature, enhancement, arch, redesign, etc.
    description: str = ""


class BacklogSignal(BaseModel):
    items: list[BacklogItem] = Field(default_factory=list)


class RepoSignalsSnapshot(BaseModel):
    recent_commits: list[CommitMetadata] = Field(default_factory=list)
    file_hotspots: list[FileHotspot] = Field(default_factory=list)
    test_signal: TestSignal
    dependency_drift: DependencyDriftSignal
    todo_signal: TodoSignal
    execution_health: ExecutionHealthSignal = Field(default_factory=ExecutionHealthSignal)
    backlog: BacklogSignal = Field(default_factory=BacklogSignal)


class RepoStateSnapshot(BaseModel):
    run_id: str
    observed_at: datetime
    observer_version: int = OBSERVER_VERSION
    source_command: str
    repo: RepoContextSnapshot
    signals: RepoSignalsSnapshot
    collector_errors: dict[str, str] = Field(default_factory=dict)
