# src/control_plane/spec_director/trigger.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from control_plane.spec_director.models import TriggerSource

logger = logging.getLogger(__name__)


@dataclass
class TriggerResult:
    source: TriggerSource
    seed_text: str


class TriggerDetector:
    def __init__(
        self,
        drop_file_path: Path,
        queue_threshold: int = 0,  # kept for config compat, not used in logic
    ) -> None:
        self._drop_file = drop_file_path

    def detect(
        self,
        ready_count: int,
        running_count: int,
        has_active_campaign: bool,
    ) -> TriggerResult | None:
        """Return a TriggerResult if a campaign should start, else None."""
        if has_active_campaign:
            return None

        # Priority 1: operator drop-file (fires regardless of board state)
        if self._drop_file.exists():
            seed = self._drop_file.read_text(encoding="utf-8").strip()
            logger.info('{"event": "spec_trigger_drop_file"}')
            return TriggerResult(source=TriggerSource.DROP_FILE, seed_text=seed)

        # Priority 2: queue drain — board must be completely idle
        if ready_count == 0 and running_count == 0:
            logger.info('{"event": "spec_trigger_queue_drain"}')
            return TriggerResult(source=TriggerSource.QUEUE_DRAIN, seed_text="")

        return None

    def archive_drop_file(self) -> None:
        """Move drop-file to archive after successful campaign creation."""
        if not self._drop_file.exists():
            return
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        archive_dir = self._drop_file.parent / "spec_direction.archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        self._drop_file.rename(archive_dir / f"{ts}.md")
        logger.info('{"event": "spec_drop_file_archived"}')
