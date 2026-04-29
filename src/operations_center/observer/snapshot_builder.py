# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import datetime

from operations_center.observer.models import RepoContextSnapshot, RepoSignalsSnapshot, RepoStateSnapshot


class SnapshotBuilder:
    def build(
        self,
        *,
        run_id: str,
        observed_at: datetime,
        source_command: str,
        repo: RepoContextSnapshot,
        signals: RepoSignalsSnapshot,
        collector_errors: dict[str, str],
    ) -> RepoStateSnapshot:
        return RepoStateSnapshot(
            run_id=run_id,
            observed_at=observed_at,
            source_command=source_command,
            repo=repo,
            signals=signals,
            collector_errors=collector_errors,
        )
