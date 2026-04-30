# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Budget state tracking for audit governance.

State is file-backed JSON under the governance state directory.
Budget tracks how many full audits may run within a rolling period.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .errors import BudgetStateError
from .file_locks import locked_state_file
from .models import AuditBudgetState, BudgetConfig


def _state_path(state_dir: Path, repo_id: str, audit_type: str) -> Path:
    return state_dir / f"{repo_id}__{audit_type}__budget.json"


def _load_budget_state_unlocked(
    path: Path,
    repo_id: str,
    audit_type: str,
    config: BudgetConfig,
) -> AuditBudgetState:
    """Load budget state without acquiring any lock (caller holds lock)."""
    if not path.exists():
        now = datetime.now(UTC)
        return AuditBudgetState(
            repo_id=repo_id,
            audit_type=audit_type,
            period_start=now,
            period_end=now + timedelta(days=config.period_days),
            max_runs=config.max_runs,
            runs_used=0,
        )
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        state = AuditBudgetState.model_validate(data)
    except Exception as exc:
        raise BudgetStateError(f"Cannot load budget state from {path}: {exc}") from exc

    now = datetime.now(UTC)
    if now > state.period_end:
        return AuditBudgetState(
            repo_id=repo_id,
            audit_type=audit_type,
            period_start=now,
            period_end=now + timedelta(days=config.period_days),
            max_runs=config.max_runs,
            runs_used=0,
        )
    return state.model_copy(update={"max_runs": config.max_runs})


def load_budget_state(
    state_dir: Path,
    repo_id: str,
    audit_type: str,
    config: BudgetConfig,
) -> AuditBudgetState:
    """Load budget state from disk, creating a fresh state if none exists.

    A fresh budget starts the period at now() for config.period_days.
    Acquires an exclusive file lock for the duration of the read.
    """
    path = _state_path(state_dir, repo_id, audit_type)
    with locked_state_file(path):
        return _load_budget_state_unlocked(path, repo_id, audit_type, config)


def save_budget_state(state: AuditBudgetState, state_dir: Path) -> None:
    """Persist budget state to disk under an exclusive file lock."""
    path = _state_path(state_dir, state.repo_id, state.audit_type)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked_state_file(path):
            path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        raise BudgetStateError(f"Cannot save budget state to {path}: {exc}") from exc


def increment_budget_after_dispatch(
    state_dir: Path,
    repo_id: str,
    audit_type: str,
    config: BudgetConfig,
    ran_at: datetime | None = None,
) -> AuditBudgetState:
    """Atomically increment runs_used after a successful dispatch.

    The read-modify-write is performed under a single exclusive file lock
    to prevent double-counting under concurrent governance runners.
    """
    path = _state_path(state_dir, repo_id, audit_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked_state_file(path):
        state = _load_budget_state_unlocked(path, repo_id, audit_type, config)
        now = ran_at or datetime.now(UTC)
        updated = state.model_copy(update={"runs_used": state.runs_used + 1, "last_run_at": now})
        path.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
    return updated


__all__ = [
    "increment_budget_after_dispatch",
    "load_budget_state",
    "save_budget_state",
]
