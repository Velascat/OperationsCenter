# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Per-task-kind reconciliation for stale Running tasks.

Cited by `docs/design/autonomy/autonomy_gaps.md` S5-3 (Per-Task-Kind Running TTL
in Reconcile). The existing `entrypoints/maintenance/recover_stale.py`
uses a single TTL for every Running task; in practice a long refactor
goal can legitimately run 2+ hours while a verification test stuck for
4 hours is well past worth reclaiming.

This module exposes ``reconcile_stale_running_issues`` — a helper that
accepts a per-kind TTL map and returns the set of issues that should be
reset. The caller (typically a maintenance CLI or watchdog) decides
whether to actually transition them.

Invariants: read-only of the issue list; mutations happen only when the
caller explicitly invokes the returned action. No new contract types.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


# Conservative defaults — operators raise per-kind via a kwarg passed by
# the maintenance CLI when they know their workload's shape.
_DEFAULT_TTLS: dict[str, int] = {
    # task-kind label value → TTL in minutes
    "goal":             4 * 60,    # 4h — refactors / migrations can take time
    "test":             45,        # 45min — verification rarely needs more
    "test_campaign":    60,
    "improve":          90,        # 90min — analysis-only typically faster
    "improve_campaign": 90,
}


@dataclass(frozen=True)
class StaleRunningCandidate:
    """A Running task whose TTL has expired for its kind."""
    task_id: str
    title: str
    task_kind: str
    age_minutes: int
    ttl_minutes: int


def _label_value(labels: list, prefix: str) -> str:
    for lab in labels or []:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip()
        if name.lower().startswith(prefix.lower() + ":"):
            return name.split(":", 1)[1].strip()
    return ""


def reconcile_stale_running_issues(
    issues: list[dict],
    *,
    ttls: dict[str, int] | None = None,
    fallback_minutes: int = 240,
    now: datetime | None = None,
) -> list[StaleRunningCandidate]:
    """Return Running tasks whose age exceeds their per-kind TTL.

    *issues* is the raw list from PlaneClient.list_issues. *ttls* maps
    task-kind label values to TTL minutes. Unknown kinds use
    *fallback_minutes*. *now* injectable for testing.

    The function does NOT call Plane — it returns candidates the caller
    transitions explicitly. This keeps the destructive action under the
    caller's control and makes the helper trivial to test.
    """
    moment = (now or datetime.now(UTC))
    ttl_map = {**_DEFAULT_TTLS, **(ttls or {})}
    out: list[StaleRunningCandidate] = []
    for issue in issues:
        st = issue.get("state")
        st_name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
        if st_name != "running":
            continue
        ts_raw = issue.get("updated_at") or issue.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        age_minutes = int((moment - ts).total_seconds() / 60)
        labels = issue.get("labels", []) or []
        kind = _label_value(labels, "task-kind") or "goal"
        ttl = int(ttl_map.get(kind, fallback_minutes))
        if age_minutes >= ttl:
            out.append(StaleRunningCandidate(
                task_id=str(issue.get("id", "")),
                title=(issue.get("name") or "")[:80],
                task_kind=kind,
                age_minutes=age_minutes,
                ttl_minutes=ttl,
            ))
    return out
