from __future__ import annotations

from pathlib import Path

from control_plane.observer.models import RepoStateSnapshot


class SnapshotLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("tools/report/control_plane/observer")

    def load(
        self,
        *,
        repo: str | None,
        snapshot_run_id: str | None,
        history_limit: int,
    ) -> list[RepoStateSnapshot]:
        snapshots = self._all_snapshots()
        if repo:
            repo_normalized = repo.strip().lower()
            snapshots = [
                snapshot
                for snapshot in snapshots
                if snapshot.repo.name.strip().lower() == repo_normalized
                or str(snapshot.repo.path).strip().lower() == repo_normalized
            ]
        if snapshot_run_id:
            matching = [snapshot for snapshot in snapshots if snapshot.run_id == snapshot_run_id]
            if not matching:
                raise ValueError(f"Snapshot run id not found: {snapshot_run_id}")
            current = matching[0]
            snapshots = [snapshot for snapshot in snapshots if snapshot.repo.path == current.repo.path]
            snapshots = [current, *[snapshot for snapshot in snapshots if snapshot.run_id != current.run_id]]
        if not snapshots:
            raise ValueError("No observer snapshots found for the requested repo/context")
        return snapshots[: history_limit + 1]

    def _all_snapshots(self) -> list[RepoStateSnapshot]:
        snapshot_paths = sorted(
            self.root.glob("*/repo_state_snapshot.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        snapshots: list[RepoStateSnapshot] = []
        for path in snapshot_paths:
            snapshots.append(RepoStateSnapshot.model_validate_json(path.read_text()))
        return snapshots
