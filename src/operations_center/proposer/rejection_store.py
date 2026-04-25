"""Long-lived proposal rejection store.

Records dedup_keys for proposals that were explicitly rejected by humans (task
manually cancelled without execution).  Unlike the 7-day rolling dedup window,
these records persist indefinitely so repeatedly-rejected proposals do not keep
re-appearing on the board.

Records are written by the improve watcher's feedback loop scan when it detects
a Cancelled task carrying a ``source: autonomy`` label whose cancellation was
NOT produced by the stale-autonomy-scan (i.e. a human did it).

Records are checked by ``ProposerGuardrailAdapter.evaluate()`` before any other
guardrail so that a once-rejected proposal is blocked immediately.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProposalRejectionStore:
    """Persistent store of human-rejected proposal dedup_keys.

    Default path: ``state/proposal_rejections.json``
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(
            os.environ.get(
                "OPERATIONS_CENTER_REJECTION_STORE_PATH",
                "state/proposal_rejections.json",
            )
        )

    # ------------------------------------------------------------------ #
    #  Public API                                                           #
    # ------------------------------------------------------------------ #

    def is_rejected(self, dedup_key: str) -> bool:
        """Return True if *dedup_key* has been permanently rejected by a human."""
        key_norm = dedup_key.strip().lower()
        for record in self._load():
            if record.get("dedup_key", "").strip().lower() == key_norm:
                return True
        return False

    def record_rejection(
        self,
        dedup_key: str,
        *,
        reason: str,
        task_id: str,
        task_title: str = "",
        now: datetime | None = None,
    ) -> None:
        """Append a permanent rejection record for *dedup_key*."""
        _now = now or datetime.now(timezone.utc)
        records = self._load()
        key_norm = dedup_key.strip().lower()
        # Deduplicate: only record once per dedup_key
        for rec in records:
            if rec.get("dedup_key", "").strip().lower() == key_norm:
                return
        records.append(
            {
                "dedup_key": dedup_key,
                "reason": reason,
                "task_id": task_id,
                "task_title": task_title,
                "recorded_at": _now.isoformat(),
            }
        )
        self._save(records)

    def all_rejections(self) -> list[dict[str, Any]]:
        """Return all rejection records (copies)."""
        return list(self._load())

    # ------------------------------------------------------------------ #
    #  Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text())
            if isinstance(raw, list):
                return raw
            return []
        except (json.JSONDecodeError, OSError):
            return []

    def _save(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(records, indent=2))
        tmp.replace(self.path)
