# src/control_plane/spec_director/trigger.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from control_plane.spec_director.models import TriggerSource

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    source: TriggerSource
    seed_text: str
    plane_task_id: str | None = None


class TriggerDetector:
    def __init__(
        self,
        drop_file_path: Path,
        plane_spec_label: str,
        queue_threshold: int,
        client: object,
    ) -> None:
        self._drop_file = drop_file_path
        self._label = plane_spec_label
        self._threshold = queue_threshold
        self._client = client

    def detect(self, ready_count: int, has_active_campaign: bool) -> TriggerResult | None:
        """Return a TriggerResult if a campaign should start, else None."""
        if has_active_campaign:
            return None

        # Priority 1: operator drop-file
        if self._drop_file.exists():
            seed = self._drop_file.read_text(encoding="utf-8").strip()
            logger.info('{"event": "spec_trigger_drop_file"}')
            return TriggerResult(source=TriggerSource.DROP_FILE, seed_text=seed)

        # Priority 2: Plane label
        label_result = self._check_plane_label()
        if label_result is not None:
            return label_result

        # Priority 3: queue drain
        if ready_count < self._threshold:
            logger.info('{"event": "spec_trigger_queue_drain", "ready_count": %d, "threshold": %d}',
                        ready_count, self._threshold)
            return TriggerResult(source=TriggerSource.QUEUE_DRAIN, seed_text="")

        return None

    def _check_plane_label(self) -> TriggerResult | None:
        try:
            issues = self._client.list_issues()
        except Exception:
            return None
        for issue in issues:
            labels = [str(lbl.get("name", "")).lower() for lbl in (issue.get("labels") or [])]
            if self._label.lower() in labels:
                state = str((issue.get("state") or {}).get("name", "")).lower()
                if state not in {"in progress", "done", "cancelled"}:
                    task_id = str(issue["id"])
                    desc = str(issue.get("description") or issue.get("description_stripped") or "")
                    logger.info('{"event": "spec_trigger_plane_label", "task_id": "%s"}', task_id)
                    return TriggerResult(
                        source=TriggerSource.PLANE_LABEL,
                        seed_text=desc.strip(),
                        plane_task_id=task_id,
                    )
        return None

    def archive_drop_file(self) -> None:
        """Move drop-file to archive after successful campaign creation."""
        if not self._drop_file.exists():
            return
        from datetime import UTC, datetime
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        archive_dir = self._drop_file.parent / "spec_direction.archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        self._drop_file.rename(archive_dir / f"{ts}.md")
        logger.info('{"event": "spec_drop_file_archived"}')
