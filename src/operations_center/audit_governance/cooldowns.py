# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Cooldown state tracking for audit governance.

State is file-backed JSON under the governance state directory.
Cooldowns prevent back-to-back full audits for the same repo/type.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .errors import CooldownStateError
from .file_locks import locked_state_file
from .models import AuditCooldownState, CooldownConfig


def _state_path(state_dir: Path, repo_id: str, audit_type: str) -> Path:
    return state_dir / f"{repo_id}__{audit_type}__cooldown.json"


def _load_cooldown_state_unlocked(
    path: Path,
    repo_id: str,
    audit_type: str,
    config: CooldownConfig,
) -> AuditCooldownState:
    """Load cooldown state without acquiring any lock (caller holds lock)."""
    if not path.exists():
        return AuditCooldownState(
            repo_id=repo_id,
            audit_type=audit_type,
            cooldown_seconds=config.cooldown_seconds,
        )
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        state = AuditCooldownState.model_validate(data)
    except Exception as exc:
        raise CooldownStateError(f"Cannot load cooldown state from {path}: {exc}") from exc
    return state.model_copy(update={"cooldown_seconds": config.cooldown_seconds})


def load_cooldown_state(
    state_dir: Path,
    repo_id: str,
    audit_type: str,
    config: CooldownConfig,
) -> AuditCooldownState:
    """Load cooldown state from disk, creating a fresh state if none exists.

    Acquires an exclusive file lock for the duration of the read.
    """
    path = _state_path(state_dir, repo_id, audit_type)
    with locked_state_file(path):
        return _load_cooldown_state_unlocked(path, repo_id, audit_type, config)


def save_cooldown_state(state: AuditCooldownState, state_dir: Path) -> None:
    """Persist cooldown state to disk under an exclusive file lock."""
    path = _state_path(state_dir, state.repo_id, state.audit_type)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked_state_file(path):
            path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise CooldownStateError(f"Cannot save cooldown state to {path}: {exc}") from exc


def update_cooldown_after_dispatch(
    state_dir: Path,
    repo_id: str,
    audit_type: str,
    config: CooldownConfig,
    ran_at: datetime | None = None,
) -> AuditCooldownState:
    """Atomically update last_run_at after a successful dispatch.

    The write is performed under an exclusive file lock so concurrent
    governance runners cannot clobber each other's cooldown timestamps.
    """
    path = _state_path(state_dir, repo_id, audit_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = ran_at or datetime.now(UTC)
    updated = AuditCooldownState(
        repo_id=repo_id,
        audit_type=audit_type,
        cooldown_seconds=config.cooldown_seconds,
        last_run_at=now,
    )
    with locked_state_file(path):
        path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    return updated


__all__ = [
    "load_cooldown_state",
    "save_cooldown_state",
    "update_cooldown_after_dispatch",
]
