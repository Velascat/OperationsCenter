# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Priority + scheduling scan helpers.

Cited by `docs/design/autonomy_gaps.md` Wave 6 (S10-10 Task Priority
Re-Evaluation Scan, S10-2 awaiting_input handling, S5-9 stale signal
handling).

Each helper is a single-pass scanner — caller decides cadence (cron,
periodic watcher tick, manual CLI). Returns structured results;
side effects only when explicitly invoked.

Invariants:
  • Read-only of Plane state by default
  • Mutations (transition_issue) only when caller passes apply=True
  • No imports of behavior_calibration
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


# ── handle_priority_rescore_scan ─────────────────────────────────────────────

@dataclass(frozen=True)
class PriorityRescoreCandidate:
    """A task that should change Plane priority based on age + signals."""
    task_id: str
    title: str
    current_priority: str
    proposed_priority: str
    reason: str


def issue_urgency_score(issue: dict, *, now: datetime | None = None) -> float:
    """Compute a 0-1 urgency score for a Plane issue.

    Components:
      • Age in Backlog state                         (older = higher)
      • Has 'lifecycle: escalated' label             (boost)
      • Has 'retry-count: N' label with N >= 1       (boost — flailing tasks)
    """
    moment = (now or datetime.now(UTC))
    score = 0.0
    # Age component
    ts_raw = issue.get("created_at") or issue.get("updated_at") or ""
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        age_days = (moment - ts).total_seconds() / 86400
        score += min(0.6, age_days / 30.0)  # caps at 0.6 for 30+ day-old tasks
    except Exception:
        pass
    # Label boosts
    labels = [
        (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        for lab in (issue.get("labels", []) or [])
    ]
    if "lifecycle: escalated" in labels:
        score += 0.3
    for lab in labels:
        if lab.startswith("retry-count:"):
            try:
                n = int(lab.split(":", 1)[1].strip())
                if n >= 1:
                    score += 0.1 * min(n, 3)  # cap at +0.3
            except ValueError:
                pass
            break
    return round(min(1.0, score), 3)


def handle_priority_rescore_scan(
    issues: list[dict],
    *,
    now: datetime | None = None,
) -> list[PriorityRescoreCandidate]:
    """Re-rank Backlog tasks by urgency_score; surface ones with stale priority.

    Pure read of the issue list — returns candidates, doesn't mutate.
    Caller decides whether to apply.

    Triggers a rescore when score >= 0.6 (high urgency) but priority is
    still 'low' / 'none' / 'medium', OR score <= 0.2 but priority is
    'urgent' / 'high'.
    """
    moment = (now or datetime.now(UTC))
    out: list[PriorityRescoreCandidate] = []
    for issue in issues:
        st = issue.get("state")
        st_name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
        if st_name != "backlog":
            continue
        score = issue_urgency_score(issue, now=moment)
        cur_pri = (issue.get("priority") or "none").lower()
        proposed = cur_pri
        reason = ""
        if score >= 0.6 and cur_pri in {"low", "none", "medium"}:
            proposed = "high"
            reason = f"urgency_score={score} but priority={cur_pri}"
        elif score <= 0.2 and cur_pri in {"high", "urgent"}:
            proposed = "low"
            reason = f"urgency_score={score} but priority={cur_pri}"
        if proposed != cur_pri:
            out.append(PriorityRescoreCandidate(
                task_id=str(issue.get("id", "")),
                title=(issue.get("name") or "")[:80],
                current_priority=cur_pri,
                proposed_priority=proposed,
                reason=reason,
            ))
    return out


# ── handle_awaiting_input_scan ───────────────────────────────────────────────

@dataclass(frozen=True)
class AwaitingInputResult:
    """A task in awaiting-input state that has new comments to act on."""
    task_id: str
    title: str
    new_comment_count: int


def handle_awaiting_input_scan(
    issues: list[dict],
    plane_client,
    *,
    state_name: str = "Awaiting Input",
) -> list[AwaitingInputResult]:
    """Find tasks in *state_name* with new comments since last scan.

    Caller decides what to do — typically transition back to Ready for AI
    so kodo retries with the new operator context.

    Best-effort: per-issue comment fetch failures are skipped silently.
    """
    out: list[AwaitingInputResult] = []
    for issue in issues:
        st = issue.get("state")
        st_label = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip()
        if st_label.lower() != state_name.lower():
            continue
        task_id = str(issue.get("id", ""))
        try:
            comments = plane_client.list_comments(task_id)
        except Exception:
            continue
        # Operator comments only — skip our own bot comments
        op_comments = [
            c for c in comments
            if not str(c.get("comment_html") or c.get("comment_stripped") or "").lower().startswith("<!-- operations-center")
        ]
        if op_comments:
            out.append(AwaitingInputResult(
                task_id=task_id,
                title=(issue.get("name") or "")[:80],
                new_comment_count=len(op_comments),
            ))
    return out


# ── signal_stale ─────────────────────────────────────────────────────────────

def signal_stale(
    snapshot_age_hours: float | None,
    *,
    threshold_hours: float = 48.0,
) -> bool:
    """Return True when an observation snapshot is older than threshold.

    Used by the propose lane: skip a candidate whose underlying signals
    are stale (the signal might have been resolved already by other work).
    """
    if snapshot_age_hours is None:
        return True  # no data = treat as stale
    return snapshot_age_hours >= threshold_hours
