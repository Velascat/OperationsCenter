# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Persisted parked-state metadata."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .parked import ParkedState


class ParkedStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ParkedState | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return ParkedState(
            root_cause_signature=str(payload["root_cause_signature"]),
            parked_reason=str(payload["parked_reason"]),
            parked_at=datetime.fromisoformat(str(payload["parked_at"])),
            unchanged_cycles=int(payload.get("unchanged_cycles", 0)),
            last_evidence_hash=payload.get("last_evidence_hash"),
            unpark_conditions=tuple(payload.get("unpark_conditions", ())),
        )

    def save(self, state: ParkedState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "root_cause_signature": state.root_cause_signature,
            "parked_reason": state.parked_reason,
            "parked_at": state.parked_at.isoformat(),
            "unchanged_cycles": state.unchanged_cycles,
            "last_evidence_hash": state.last_evidence_hash,
            "unpark_conditions": list(state.unpark_conditions),
        }
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
