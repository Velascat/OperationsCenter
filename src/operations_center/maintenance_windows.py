# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Maintenance window evaluation.

A maintenance window is a recurring time-of-day + weekday range during
which autonomous execution should be paused. The schema lives in
``Settings.maintenance_windows`` (a list of MaintenanceWindow objects).

Cited by `docs/design/autonomy_gaps.md` S6-1 / S7-7. The check itself
was inline in ``autonomy_cycle/main.py``; this module extracts it so
other components (escalation logic, status pane, future schedulers)
can ask the same question without duplicating logic.

Pure functions, no side effects, no settings mutation. Per the
anti-collapse invariant: this is a read of config + clock; nothing
about behavior_calibration or runtime feedback.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _in_maintenance_window(settings: Any, now: datetime | None = None) -> bool:
    """Return True when *now* falls inside any configured maintenance window.

    Each window has:
      • days: list of weekday ints (0=Mon..6=Sun); empty / missing = every day
      • start_hour, end_hour: UTC integers 0-23 (end exclusive). When
        start > end, the window wraps midnight (e.g. 22 → 4).

    Defensive: missing fields default to 0 / empty so a partially-filled
    config doesn't crash the loop.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    windows = getattr(settings, "maintenance_windows", []) or []
    for w in windows:
        days = list(getattr(w, "days", []) or [])
        if days and moment.weekday() not in days:
            continue
        start = int(getattr(w, "start_hour", 0))
        end = int(getattr(w, "end_hour", 0))
        if start == end:
            # Zero-width window — treat as misconfigured / never active rather
            # than always-active (which would happen if we let it fall to the
            # wrap-midnight branch).
            continue
        h = moment.hour
        if start < end:
            in_window = start <= h < end
        else:
            # Wraps midnight (e.g. 22 → 04)
            in_window = h >= start or h < end
        if in_window:
            return True
    return False
