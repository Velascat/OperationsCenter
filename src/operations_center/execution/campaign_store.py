# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""S10-4: Campaign/project tracking store.

A *campaign* is a named multi-step execution plan created by
``build_multi_step_plan()``.  The store tracks each campaign's overall
progress — how many steps are Done vs. total — so the operator can see
aggregate progress across related tasks without manually cross-referencing
Plane.

Campaigns are identified by the source task ID (the parent task that was
decomposed).  The store is backed by ``state/campaigns.json`` and updated
each time a step task transitions to Done or is cancelled.

Usage::

    store = CampaignStore()
    campaign_id = store.create(
        source_task_id="abc123",
        title="Refactor auth middleware",
        step_task_ids=["s1", "s2", "s3"],
    )
    store.record_step_done("abc123", step_task_id="s1")
    for row in store.list_campaigns():
        print(row)
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_STORE_PATH = Path("state/campaigns.json")


@dataclass
class CampaignRecord:
    """Tracks a single multi-step campaign."""
    source_task_id: str
    title: str
    step_task_ids: list[str]
    done_step_ids: list[str]
    cancelled_step_ids: list[str]
    created_at: str
    updated_at: str
    status: str  # "in_progress", "completed", "partial", "cancelled"

    @property
    def total_steps(self) -> int:
        return len(self.step_task_ids)

    @property
    def completed_steps(self) -> int:
        return len(self.done_step_ids)

    @property
    def progress_pct(self) -> float:
        if not self.step_task_ids:
            return 0.0
        return round(self.completed_steps / self.total_steps * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["total_steps"] = self.total_steps
        d["completed_steps"] = self.completed_steps
        d["progress_pct"] = self.progress_pct
        return d


class CampaignStore:
    """Thread-safe store for multi-step campaign progress records."""

    def __init__(self, path: Path = _DEFAULT_STORE_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()

    def create(
        self,
        *,
        source_task_id: str,
        title: str,
        step_task_ids: list[str],
        now: datetime | None = None,
    ) -> str:
        """Register a new campaign and return its *source_task_id* as the campaign ID.

        If a campaign with the same *source_task_id* already exists, the call is a
        no-op and the existing ID is returned.
        """
        _now = (now or datetime.now(UTC)).isoformat()
        with self._lock:
            campaigns = self._load()
            if source_task_id in campaigns:
                return source_task_id
            campaigns[source_task_id] = CampaignRecord(
                source_task_id=source_task_id,
                title=title,
                step_task_ids=list(step_task_ids),
                done_step_ids=[],
                cancelled_step_ids=[],
                created_at=_now,
                updated_at=_now,
                status="in_progress",
            ).to_dict()
            self._save(campaigns)
        return source_task_id

    def record_step_done(
        self,
        source_task_id: str,
        *,
        step_task_id: str,
        now: datetime | None = None,
    ) -> None:
        """Mark *step_task_id* as completed within the campaign."""
        _now = (now or datetime.now(UTC)).isoformat()
        with self._lock:
            campaigns = self._load()
            record = campaigns.get(source_task_id)
            if record is None:
                return
            if step_task_id not in record["done_step_ids"]:
                record["done_step_ids"].append(step_task_id)
            record["updated_at"] = _now
            record["status"] = _compute_status(record)
            _refresh_computed(record)
            self._save(campaigns)

    def record_step_cancelled(
        self,
        source_task_id: str,
        *,
        step_task_id: str,
        now: datetime | None = None,
    ) -> None:
        """Mark *step_task_id* as cancelled within the campaign."""
        _now = (now or datetime.now(UTC)).isoformat()
        with self._lock:
            campaigns = self._load()
            record = campaigns.get(source_task_id)
            if record is None:
                return
            if step_task_id not in record["cancelled_step_ids"]:
                record["cancelled_step_ids"].append(step_task_id)
            record["updated_at"] = _now
            record["status"] = _compute_status(record)
            _refresh_computed(record)
            self._save(campaigns)

    def get(self, source_task_id: str) -> dict[str, Any] | None:
        """Return the raw campaign record dict or None."""
        with self._lock:
            return dict(self._load().get(source_task_id) or {}) or None

    def list_campaigns(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """Return all campaign records, optionally filtered by *status*."""
        with self._lock:
            campaigns = self._load()
        rows = list(campaigns.values())
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning('{"event": "campaign_store_load_failed", "path": "%s", "error": "%s"}', self._path, exc)
        return {}

    def _save(self, campaigns: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(campaigns, indent=2, ensure_ascii=False), encoding="utf-8")


def _compute_status(record: dict[str, Any]) -> str:
    """Derive campaign status from step completion counts."""
    total = len(record.get("step_task_ids", []))
    done = len(record.get("done_step_ids", []))
    cancelled = len(record.get("cancelled_step_ids", []))
    if done >= total and total > 0:
        return "completed"
    if cancelled >= total and total > 0:
        return "cancelled"
    if (done + cancelled) > 0:
        return "partial"
    return "in_progress"


def _refresh_computed(record: dict[str, Any]) -> None:
    """Update the computed summary fields (total_steps, completed_steps, progress_pct) in *record*."""
    total = len(record.get("step_task_ids", []))
    done = len(record.get("done_step_ids", []))
    record["total_steps"] = total
    record["completed_steps"] = done
    record["progress_pct"] = round(done / total * 100, 1) if total > 0 else 0.0
