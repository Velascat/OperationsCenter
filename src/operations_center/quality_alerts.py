# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Helpers for surfacing kodo run quality issues + escalation events.

Cited by `docs/design/autonomy/autonomy_gaps.md` Wave 5 (S5-10 Quality Erosion,
S7-7 Escalation Wiring, S10-1 Rejection Patterns Injected into Kodo
Prompts).

Pure formatting / extraction utilities — no I/O. Callers wire them at
the natural integration points (Plane comments, kodo prompt assembly).

Invariants: read-only utilities, no contract mutation, no
behavior_calibration imports.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# ── _comment_markdown ────────────────────────────────────────────────────────

def _comment_markdown(
    *,
    headline: str,
    bullets: list[str] | None = None,
    code_block: str | None = None,
    bot_marker: str = "<!-- operations-center:bot -->",
) -> str:
    """Compose a markdown comment with bot-marker, headline, optional bullets + code.

    Used to format Plane / GitHub comments consistently. The bot marker
    is what `pr_review_watcher` and others look for to skip their own
    comments when polling.
    """
    parts: list[str] = [bot_marker, "", f"**{headline.strip()}**"]
    if bullets:
        parts.append("")
        for b in bullets:
            line = (b or "").strip()
            if line:
                parts.append(f"- {line}")
    if code_block:
        parts.extend(["", "```", code_block.strip(), "```"])
    return "\n".join(parts)


# ── _extract_rejection_patterns + _load_rejection_patterns_for_proposal ──────

def _extract_rejection_patterns(rejection_records: list[dict]) -> list[str]:
    """Pull the most-cited rejection reasons across recent records.

    Each record is a dict with at least a `reason` field. Returns the top
    N reasons by count, deduplicated and lowercased — these become hints
    for the kodo prompt to avoid repeating the same mistake.
    """
    if not rejection_records:
        return []
    counts: dict[str, int] = {}
    for rec in rejection_records:
        if not isinstance(rec, dict):
            continue
        reason = (rec.get("reason") or "").strip().lower()
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    # Top 5 most-cited
    return [r for r, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:5]]


def _load_rejection_patterns_for_proposal(repo_key: str | None = None) -> list[str]:
    """Read recent rejection records and return the top patterns.

    Reads `state/proposal_rejections.json` (top-level catalog) — which
    aggregates across all repos. If the catalog is missing or unreadable,
    returns an empty list (best-effort hint, not a hard requirement).
    """
    catalog = Path("state/proposal_rejections.json")
    if not catalog.exists():
        return []
    try:
        data = json.loads(catalog.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("rejection-patterns: catalog unreadable — %s", exc)
        return []
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        return []
    if repo_key:
        records = [r for r in records if isinstance(r, dict) and r.get("repo_key") == repo_key]
    return _extract_rejection_patterns(records)


# ── _escalate_to_human ───────────────────────────────────────────────────────

_ESCALATION_LOG = Path("state/escalations.jsonl")


def _escalate_to_human(
    *,
    task_id: str,
    reason: str,
    detail: str = "",
    severity: str = "warn",
) -> bool:
    """Emit a structured escalation record + log entry.

    Appends a JSON line to `state/escalations.jsonl` so external
    integrations (status pane red banner, future alerter) can pick it
    up. Returns True on success, False if the write failed.

    No external notification today — that's a separate feature (alerter
    + Slack/email integration). This is the durable record.
    """
    payload = {
        "ts":       datetime.now(UTC).isoformat(),
        "task_id":  task_id,
        "reason":   reason,
        "detail":   (detail or "")[:1000],
        "severity": severity,
    }
    try:
        _ESCALATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _ESCALATION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.warning("_escalate_to_human: failed to write escalation — %s", exc)
        return False


# ── _process_self_review (reviewer-side helper) ──────────────────────────────

def _process_self_review(verdict: dict | None, *, max_summary: int = 400) -> tuple[str, str]:
    """Normalise a kodo self-review verdict dict into (result, summary).

    The reviewer pipeline reads `verdict.json` from kodo's workspace; this
    helper validates the shape and returns trimmed strings the caller can
    safely use in PR comments / state files.

    Returns ("LGTM" | "CONCERNS", summary). Defaults to CONCERNS when
    the verdict is missing / malformed (fail-closed).
    """
    if not isinstance(verdict, dict):
        return "CONCERNS", "(verdict missing or malformed)"
    raw_result = str(verdict.get("result") or "").strip().upper()
    if raw_result not in {"LGTM", "CONCERNS"}:
        raw_result = "CONCERNS"
    summary = str(verdict.get("summary") or "").strip()
    if len(summary) > max_summary:
        summary = summary[: max_summary - 3] + "..."
    return raw_result, (summary or "(no summary provided)")
