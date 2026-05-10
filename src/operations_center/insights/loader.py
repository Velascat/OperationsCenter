# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from operations_center.observer.models import RepoStateSnapshot


class SnapshotLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("tools/report/operations_center/observer")

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

    def latest_snapshot_age_hours(self, *, repo: str | None = None) -> float | None:
        """Return how many hours ago the most recent snapshot was written.

        Returns ``None`` when no snapshots exist.  Optionally filters by repo
        name so the caller can check staleness for a specific target.
        """
        paths = sorted(
            self.root.glob("*/repo_state_snapshot.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not paths:
            return None
        if repo:
            repo_norm = repo.strip().lower()
            for path in paths:
                try:
                    snap = RepoStateSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
                    if snap.repo.name.strip().lower() == repo_norm or str(snap.repo.path).strip().lower() == repo_norm:
                        mtime = path.stat().st_mtime
                        age = (datetime.now(timezone.utc).timestamp() - mtime) / 3600
                        return round(age, 2)
                except Exception:
                    continue
            return None
        # Newest snapshot overall
        mtime = paths[0].stat().st_mtime
        age = (datetime.now(timezone.utc).timestamp() - mtime) / 3600
        return round(age, 2)

    def _all_snapshots(self) -> list[RepoStateSnapshot]:
        snapshot_paths = sorted(
            self.root.glob("*/repo_state_snapshot.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        snapshots: list[RepoStateSnapshot] = []
        for path in snapshot_paths:
            snapshots.append(RepoStateSnapshot.model_validate_json(path.read_text(encoding="utf-8")))
        return snapshots
