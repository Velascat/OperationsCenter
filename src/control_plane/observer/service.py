from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from control_plane.config import Settings
from control_plane.observer.artifact_writer import ObserverArtifactWriter
from control_plane.observer.models import (
    BacklogSignal,
    DependencyDriftSignal,
    ExecutionHealthSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal,
    TodoSignal,
)
from control_plane.observer.snapshot_builder import SnapshotBuilder


@dataclass(frozen=True)
class ObserverContext:
    repo_path: Path
    repo_name: str
    base_branch: str | None
    run_id: str
    observed_at: datetime
    source_command: str
    settings: Settings
    commit_limit: int
    hotspot_window: int
    todo_limit: int
    logs_root: Path


class RepoSignalCollector(Protocol):
    def collect(self, context: ObserverContext) -> Any:
        ...


class RepoObserverService:
    def __init__(
        self,
        *,
        repo_collector: RepoSignalCollector,
        recent_commits_collector: RepoSignalCollector,
        file_hotspots_collector: RepoSignalCollector,
        test_signal_collector: RepoSignalCollector,
        dependency_drift_collector: RepoSignalCollector,
        todo_signal_collector: RepoSignalCollector,
        execution_health_collector: RepoSignalCollector | None = None,
        backlog_collector: RepoSignalCollector | None = None,
        snapshot_builder: SnapshotBuilder | None = None,
        artifact_writer: ObserverArtifactWriter | None = None,
    ) -> None:
        self.repo_collector = repo_collector
        self.recent_commits_collector = recent_commits_collector
        self.file_hotspots_collector = file_hotspots_collector
        self.test_signal_collector = test_signal_collector
        self.dependency_drift_collector = dependency_drift_collector
        self.todo_signal_collector = todo_signal_collector
        self.execution_health_collector = execution_health_collector
        self.backlog_collector = backlog_collector
        self.snapshot_builder = snapshot_builder or SnapshotBuilder()
        self.artifact_writer = artifact_writer or ObserverArtifactWriter()

    def observe(self, context: ObserverContext) -> tuple[RepoStateSnapshot, list[str]]:
        collector_errors: dict[str, str] = {}
        repo_snapshot = self._collect_required(self.repo_collector, context, "repo_context", collector_errors)
        recent_commits = self._collect_optional(self.recent_commits_collector, context, "recent_commits", collector_errors, default=[])
        file_hotspots = self._collect_optional(self.file_hotspots_collector, context, "file_hotspots", collector_errors, default=[])
        test_signal = self._collect_optional(
            self.test_signal_collector,
            context,
            "test_signal",
            collector_errors,
            default=TestSignal(status="unknown"),
        )
        dependency_drift = self._collect_optional(
            self.dependency_drift_collector,
            context,
            "dependency_drift",
            collector_errors,
            default=DependencyDriftSignal(status="not_available"),
        )
        todo_signal = self._collect_optional(
            self.todo_signal_collector,
            context,
            "todo_signal",
            collector_errors,
            default=TodoSignal(),
        )
        execution_health = (
            self._collect_optional(
                self.execution_health_collector,
                context,
                "execution_health",
                collector_errors,
                default=ExecutionHealthSignal(),
            )
            if self.execution_health_collector is not None
            else ExecutionHealthSignal()
        )
        backlog = (
            self._collect_optional(
                self.backlog_collector,
                context,
                "backlog",
                collector_errors,
                default=BacklogSignal(),
            )
            if self.backlog_collector is not None
            else BacklogSignal()
        )

        signals = RepoSignalsSnapshot(
            recent_commits=recent_commits,
            file_hotspots=file_hotspots,
            test_signal=test_signal,
            dependency_drift=dependency_drift,
            todo_signal=todo_signal,
            execution_health=execution_health,
            backlog=backlog,
        )
        snapshot = self.snapshot_builder.build(
            run_id=context.run_id,
            observed_at=context.observed_at,
            source_command=context.source_command,
            repo=repo_snapshot,
            signals=signals,
            collector_errors=collector_errors,
        )
        artifacts = self.artifact_writer.write(snapshot)
        return snapshot, artifacts

    def _collect_required(
        self,
        collector: RepoSignalCollector,
        context: ObserverContext,
        name: str,
        collector_errors: dict[str, str],
    ) -> RepoContextSnapshot:
        try:
            result = collector.collect(context)
        except Exception as exc:
            collector_errors[name] = str(exc)
            raise
        return result

    def _collect_optional(
        self,
        collector: RepoSignalCollector,
        context: ObserverContext,
        name: str,
        collector_errors: dict[str, str],
        *,
        default: Any,
    ) -> Any:
        try:
            return collector.collect(context)
        except Exception as exc:
            collector_errors[name] = str(exc)
            return default


def new_observer_context(
    *,
    repo_path: Path,
    repo_name: str,
    base_branch: str | None,
    settings: Settings,
    source_command: str,
    commit_limit: int,
    hotspot_window: int,
    todo_limit: int,
    logs_root: Path,
) -> ObserverContext:
    observed_at = datetime.now(UTC)
    run_id = f"obs_{observed_at.strftime('%Y%m%dT%H%M%SZ')}_{observed_at.microsecond:06x}"[-31:]
    return ObserverContext(
        repo_path=repo_path,
        repo_name=repo_name,
        base_branch=base_branch,
        run_id=run_id,
        observed_at=observed_at,
        source_command=source_command,
        settings=settings,
        commit_limit=commit_limit,
        hotspot_window=hotspot_window,
        todo_limit=todo_limit,
        logs_root=logs_root,
    )
