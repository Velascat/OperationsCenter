# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
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


class CheckSignal(BaseModel):
    status: str
    test_count: int | None = None
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
    unknown_count: int = 0
    error_count: int = 0
    validation_failed_count: int = 0
    recent_runs: list[ExecutionRunRecord] = Field(default_factory=list)


class BacklogItem(BaseModel):
    title: str
    item_type: str  # maintenance, feature, enhancement, arch, redesign, etc.
    description: str = ""


class BacklogSignal(BaseModel):
    items: list[BacklogItem] = Field(default_factory=list)


class LintViolation(BaseModel):
    path: str
    line: int
    col: int
    code: str
    message: str


class LintSignal(BaseModel):
    status: str  # "clean", "violations", "unavailable"
    violation_count: int = 0
    distinct_file_count: int = 0
    top_violations: list[LintViolation] = Field(default_factory=list)
    source: str | None = None


class TypeError(BaseModel):
    path: str
    line: int
    col: int
    code: str
    message: str


class TypeSignal(BaseModel):
    status: str  # "clean", "errors", "unavailable"
    error_count: int = 0
    distinct_file_count: int = 0
    top_errors: list[TypeError] = Field(default_factory=list)
    source: str | None = None


class ValidationFailureRecord(BaseModel):
    task_id: str
    worker_role: str
    total_runs: int
    validation_failure_count: int
    failure_rate: float = 0.0


class ValidationHistorySignal(BaseModel):
    status: str  # "nominal", "patterns_detected", "unavailable"
    tasks_analyzed: int = 0
    tasks_with_repeated_failures: list[ValidationFailureRecord] = Field(default_factory=list)
    overall_failure_rate: float = 0.0
    source: str | None = None


class CICheckRunRecord(BaseModel):
    name: str
    sha: str
    conclusion: str  # success, failure, timed_out, cancelled, skipped, neutral, etc.


class CIHistorySignal(BaseModel):
    status: str  # "nominal", "flaky", "failing", "unavailable"
    runs_checked: int = 0
    failure_rate: float = 0.0
    flaky_checks: list[str] = Field(default_factory=list)
    failing_checks: list[str] = Field(default_factory=list)
    recent_runs: list[CICheckRunRecord] = Field(default_factory=list)
    source: str | None = None


class ArchitectureSignal(BaseModel):
    status: str  # "healthy", "warnings", "unavailable"
    source: str | None = None
    observed_at: datetime | None = None
    max_import_depth: int | None = None
    circular_dependencies: list[str] = Field(default_factory=list)
    coupling_score: float | None = None
    summary: str | None = None


class BenchmarkSignal(BaseModel):
    status: str  # "nominal", "regression", "unavailable"
    source: str | None = None
    observed_at: datetime | None = None
    benchmark_count: int = 0
    regressions: list[str] = Field(default_factory=list)
    summary: str | None = None


class SecuritySignal(BaseModel):
    status: str  # "clean", "advisories", "unavailable"
    source: str | None = None
    observed_at: datetime | None = None
    advisory_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    summary: str | None = None


class UncoveredFile(BaseModel):
    path: str
    coverage_pct: float


class CoverageSignal(BaseModel):
    status: str  # "measured", "partial", "unavailable"
    total_coverage_pct: float | None = None
    uncovered_file_count: int = 0
    uncovered_threshold_pct: float = 80.0
    top_uncovered: list[UncoveredFile] = Field(default_factory=list)
    source: str | None = None
    observed_at: datetime | None = None
    summary: str | None = None


class RepoSignalsSnapshot(BaseModel):
    recent_commits: list[CommitMetadata] = Field(default_factory=list)
    file_hotspots: list[FileHotspot] = Field(default_factory=list)
    test_signal: CheckSignal
    dependency_drift: DependencyDriftSignal
    todo_signal: TodoSignal
    execution_health: ExecutionHealthSignal = Field(default_factory=ExecutionHealthSignal)
    backlog: BacklogSignal = Field(default_factory=BacklogSignal)
    lint_signal: LintSignal = Field(default_factory=lambda: LintSignal(status="unavailable"))
    type_signal: TypeSignal = Field(default_factory=lambda: TypeSignal(status="unavailable"))
    ci_history: CIHistorySignal = Field(default_factory=lambda: CIHistorySignal(status="unavailable"))
    validation_history: ValidationHistorySignal = Field(default_factory=lambda: ValidationHistorySignal(status="unavailable"))
    architecture_signal: ArchitectureSignal = Field(default_factory=lambda: ArchitectureSignal(status="unavailable"))
    benchmark_signal: BenchmarkSignal = Field(default_factory=lambda: BenchmarkSignal(status="unavailable"))
    security_signal: SecuritySignal = Field(default_factory=lambda: SecuritySignal(status="unavailable"))
    coverage_signal: CoverageSignal = Field(default_factory=lambda: CoverageSignal(status="unavailable"))


class RepoStateSnapshot(BaseModel):
    run_id: str
    observed_at: datetime
    observer_version: int = OBSERVER_VERSION
    source_command: str
    repo: RepoContextSnapshot
    signals: RepoSignalsSnapshot
    collector_errors: dict[str, str] = Field(default_factory=dict)
