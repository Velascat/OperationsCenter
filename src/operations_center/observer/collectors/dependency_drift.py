from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from operations_center.observer.models import DependencyDriftSignal
from operations_center.observer.service import ObserverContext


class DependencyDriftCollector:
    def collect(self, context: ObserverContext) -> DependencyDriftSignal:
        candidate = self._latest_dependency_report(context.settings.report_root)
        if candidate is None:
            return DependencyDriftSignal(status="not_available")

        payload = json.loads(candidate.read_text())
        statuses = payload.get("statuses", [])
        created_task_ids = payload.get("created_task_ids", [])
        actionable = [
            status
            for status in statuses
            if isinstance(status, dict) and status.get("notes")
        ]
        summary = (
            f"actionable_statuses={len(actionable)} created_task_ids={len(created_task_ids)}"
            if statuses
            else "dependency report present with no statuses"
        )
        return DependencyDriftSignal(
            status="available",
            source=str(candidate),
            observed_at=datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC),
            summary=summary,
        )

    def _latest_dependency_report(self, report_root: Path) -> Path | None:
        candidates = sorted(report_root.glob("*/dependency_report.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None
