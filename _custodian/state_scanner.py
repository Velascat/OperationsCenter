# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""StateScanner implementation for OperationsCenter state files.

Implements Custodian's StateScanner protocol over OC's
``state/proposal_feedback/<uuid>.json``,
``state/pr_reviews/<uuid>.json``,
``state/improve_insights/<uuid>.json`` etc.

Each file is keyed by a UUID matching the originating Plane task. The
``is_terminal`` check returns True when:
  - the record carries an explicit terminal status field, OR
  - the record's task no longer exists on Plane (handled by the caller
    that supplies the Plane-side state map).

This scanner is consumed by Custodian's maintenance utilities; the
corresponding OC-specific maintenance CLI lives in
``src/operations_center/entrypoints/maintenance/cleanup_state.py``.
"""
from __future__ import annotations


# Plane states that mean "the task is done with whatever it was going to do"
_TERMINAL_PLANE_STATES = frozenset({"done", "blocked", "cancelled"})

# Per-record status fields that count as terminal regardless of Plane state.
_TERMINAL_RECORD_STATUSES = frozenset({
    "done", "complete", "completed", "cancelled", "abandoned",
    "merged",   # pr_reviews
    "rejected", # proposal_feedback
})


class OCStateScanner:
    """Knows OC's per-task state file layout."""

    state_subdir: str = "state"

    # Subdirectories under state_subdir that hold per-task records keyed by
    # UUID. The cleanup CLI walks each of these.
    per_task_subdirs: tuple[str, ...] = (
        "proposal_feedback",
        "pr_reviews",
        "improve_insights",
    )

    def is_terminal(self, record: dict) -> bool:
        """True when the record's owning task is finished from OC's side."""
        if not isinstance(record, dict):
            return False
        # 1. Explicit per-record status field
        for key in ("status", "outcome", "phase"):
            value = record.get(key)
            if isinstance(value, str) and value.strip().lower() in _TERMINAL_RECORD_STATUSES:
                return True
        # 2. Plane-state proxy: when the record was hydrated against Plane
        # the cleanup tool stamps `plane_state` so we can decide here.
        plane_state = record.get("plane_state")
        if isinstance(plane_state, str):
            normalized = plane_state.strip().lower()
            if normalized in _TERMINAL_PLANE_STATES or normalized == "unknown":
                # "unknown" means the task is gone from Plane → safe to treat
                # the record as orphaned terminal.
                return True
        return False
