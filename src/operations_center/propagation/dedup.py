# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Idempotency for propagation — JSON sidecar in state/.

Dedup key: ``(target_repo_id, consumer_repo_id, target_version_or_sha)``.

Two semantics combine:
- **Exact-key suppression**: same (target, consumer, version) seen twice
  in any window → skip the second.
- **Time-window suppression**: even within the same version, don't fire
  twice in the configured window (default 24h).

The store is a single JSON file. Concurrent writers use the existing
file-lock helper from `audit_governance/file_locks` so cross-process
safety lines up with the rest of the system.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DedupKey:
    """One propagation-pair key.

    ``target_version`` may be a git SHA, semver tag, ISO timestamp, or
    any operator-chosen identifier. The store treats it as opaque — same
    string = same version.
    """

    target_repo_id: str
    consumer_repo_id: str
    target_version: str

    def serialize(self) -> str:
        """Stable string form for JSON storage. Pipe-separated."""
        return f"{self.target_repo_id}|{self.consumer_repo_id}|{self.target_version}"


@dataclass
class PropagationDedupStore:
    """JSON-backed dedup index.

    Schema on disk:
        {"version": 1, "entries": {"<serialized-key>": "<iso-timestamp>"}}

    Thread/process safety: callers should wrap mutation under a file
    lock when running concurrently. The default OC use site is the
    propagator entrypoint, which is single-process today.
    """

    path: Path

    def load(self) -> dict[str, str]:
        """Return the {serialized_key: iso_timestamp} map. Empty if missing/corrupt."""
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(payload, dict):
            return {}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return {}
        return {str(k): str(v) for k, v in entries.items()}

    def is_recent(
        self,
        key: DedupKey,
        *,
        window_hours: int,
        now: datetime | None = None,
    ) -> bool:
        """True if the key fired within ``window_hours``. Used to suppress."""
        entries = self.load()
        last_iso = entries.get(key.serialize())
        if last_iso is None:
            return False
        try:
            last = datetime.fromisoformat(last_iso)
        except ValueError:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=window_hours)
        return last >= cutoff

    def record(
        self,
        key: DedupKey,
        *,
        now: datetime | None = None,
    ) -> None:
        """Stamp the key with `now` and persist atomically (write-then-rename)."""
        entries = self.load()
        timestamp = (now or datetime.now(timezone.utc)).isoformat()
        entries[key.serialize()] = timestamp
        payload: dict[str, Any] = {"version": 1, "entries": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


__all__ = ["DedupKey", "PropagationDedupStore"]
