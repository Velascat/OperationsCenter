from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, Sequence

from control_plane.insights.artifact_writer import InsightArtifactWriter
from control_plane.insights.loader import SnapshotLoader
from control_plane.insights.models import InsightRepoRef, RepoInsightsArtifact, SourceSnapshotRef
from control_plane.observer.models import RepoStateSnapshot


class InsightDeriver(Protocol):
    def derive(self, snapshots: Sequence[RepoStateSnapshot]):
        ...


@dataclass(frozen=True)
class InsightGenerationContext:
    repo_filter: str | None
    snapshot_run_id: str | None
    history_limit: int
    run_id: str
    generated_at: datetime
    source_command: str


class InsightEngineService:
    def __init__(
        self,
        *,
        loader: SnapshotLoader,
        derivers: list[InsightDeriver],
        artifact_writer: InsightArtifactWriter | None = None,
    ) -> None:
        self.loader = loader
        self.derivers = derivers
        self.artifact_writer = artifact_writer or InsightArtifactWriter()

    def generate(self, context: InsightGenerationContext) -> tuple[RepoInsightsArtifact, list[str]]:
        snapshots = self.loader.load(
            repo=context.repo_filter,
            snapshot_run_id=context.snapshot_run_id,
            history_limit=context.history_limit,
        )
        current = snapshots[0]
        insights = []
        for deriver in self.derivers:
            insights.extend(deriver.derive(snapshots))
        artifact = RepoInsightsArtifact(
            run_id=context.run_id,
            generated_at=context.generated_at,
            source_command=context.source_command,
            repo=InsightRepoRef(name=current.repo.name, path=current.repo.path),
            source_snapshots=[
                SourceSnapshotRef(run_id=snapshot.run_id, observed_at=snapshot.observed_at)
                for snapshot in snapshots
            ],
            insights=insights,
        )
        return artifact, self.artifact_writer.write(artifact)


def new_generation_context(
    *,
    repo_filter: str | None,
    snapshot_run_id: str | None,
    history_limit: int,
    source_command: str,
) -> InsightGenerationContext:
    generated_at = datetime.now(UTC)
    run_id = f"ins_{generated_at.strftime('%Y%m%dT%H%M%SZ')}_{generated_at.microsecond:06x}"[-31:]
    return InsightGenerationContext(
        repo_filter=repo_filter,
        snapshot_run_id=snapshot_run_id,
        history_limit=history_limit,
        run_id=run_id,
        generated_at=generated_at,
        source_command=source_command,
    )
