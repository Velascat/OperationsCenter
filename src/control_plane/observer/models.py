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


class RepoSignalsSnapshot(BaseModel):
    recent_commits: list[CommitMetadata] = Field(default_factory=list)
    file_hotspots: list[FileHotspot] = Field(default_factory=list)
    test_signal: TestSignal
    dependency_drift: DependencyDriftSignal
    todo_signal: TodoSignal


class RepoStateSnapshot(BaseModel):
    run_id: str
    observed_at: datetime
    observer_version: int = OBSERVER_VERSION
    source_command: str
    repo: RepoContextSnapshot
    signals: RepoSignalsSnapshot
    collector_errors: dict[str, str] = Field(default_factory=dict)
