# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Periodic triage scan — priority rescore + awaiting-input unblock.

Wraps `operations_center.priority_scans` helpers into an operator CLI.
Two scans run together:

  1. handle_priority_rescore_scan — flags Backlog tasks whose Plane
     priority is stale relative to their urgency (old + escalated +
     retrying). With --apply, transitions them.

  2. handle_awaiting_input_scan — flags tasks in Awaiting Input state
     that have new operator comments. With --apply, transitions them
     to Ready for AI so kodo retries with the new context.

    python -m operations_center.entrypoints.maintenance.triage_scan \\
        --config config/operations_center.local.yaml \\
        [--apply] [--awaiting-input-state "Awaiting Input"]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.priority_scans import (
    handle_awaiting_input_scan,
    handle_priority_rescore_scan,
)
from operations_center.queue_healing import QueueHealingEngine, QueueHealingTask, QueueTransition


def main() -> int:
    parser = argparse.ArgumentParser(description="Periodic triage: priority + awaiting-input")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--apply", action="store_true",
                        help="apply transitions (default: dry-run)")
    parser.add_argument("--awaiting-input-state", default="Awaiting Input")
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    try:
        items = client.list_issues()
    except Exception as exc:
        client.close()
        print(json.dumps({"error": f"plane_fetch_failed: {exc}"}, ensure_ascii=False))
        return 1

    now = datetime.now(UTC)
    rescore_candidates = handle_priority_rescore_scan(items, now=now)
    awaiting = handle_awaiting_input_scan(items, client, state_name=args.awaiting_input_state)
    queue_healing = _queue_healing_actions(items, now=now)

    rescore_actions: list[dict] = []
    for c in rescore_candidates:
        entry = {
            "task_id": c.task_id, "title": c.title,
            "current_priority": c.current_priority,
            "proposed_priority": c.proposed_priority,
            "reason": c.reason,
        }
        if args.apply:
            try:
                # Plane's priority field is set via the same PATCH path as state
                # transitions; the existing client doesn't expose a typed
                # set_priority, so we use the raw underlying client call here.
                client._client.patch(  # type: ignore[attr-defined]
                    f"/api/v1/workspaces/{client.workspace_slug}"
                    f"/projects/{client.project_id}/work-items/{c.task_id}/",
                    json={"priority": c.proposed_priority},
                )
                entry["action"] = "applied"
            except Exception as exc:
                entry["action"] = "error"
                entry["error"] = str(exc)
        else:
            entry["action"] = "would_apply"
        rescore_actions.append(entry)

    awaiting_actions: list[dict] = []
    for a in awaiting:
        entry = {
            "task_id": a.task_id, "title": a.title,
            "new_comment_count": a.new_comment_count,
        }
        if args.apply:
            try:
                client.transition_issue(a.task_id, "Ready for AI")
                client.comment_issue(
                    a.task_id,
                    f"Triage scan: {a.new_comment_count} new operator comment(s) — "
                    "re-promoted to Ready for AI for another kodo pass.",
                )
                entry["action"] = "transitioned"
            except Exception as exc:
                entry["action"] = "error"
                entry["error"] = str(exc)
        else:
            entry["action"] = "would_transition"
        awaiting_actions.append(entry)

    queue_healing_actions: list[dict] = []
    for decision in queue_healing:
        entry = {
            "task_id": decision.task_id,
            "transition": decision.transition.value,
            "reason": decision.reason,
            "retry_lineage_id": decision.retry_lineage_id,
            "safe": decision.safe,
            "escalate": decision.escalate,
        }
        if decision.transition == QueueTransition.NONE:
            entry["action"] = "none"
        elif decision.escalate:
            entry["action"] = "escalate"
            if args.apply:
                try:
                    client.comment_issue(
                        decision.task_id,
                        "Queue healing escalation: "
                        f"{decision.reason} (lineage={decision.retry_lineage_id or 'unknown'}).",
                    )
                    entry["action"] = "escalation_commented"
                except Exception as exc:
                    entry["action"] = "error"
                    entry["error"] = str(exc)
        elif args.apply and decision.safe:
            target_state = (
                "Ready for AI"
                if decision.transition == QueueTransition.BLOCKED_TO_READY_FOR_AI
                else "Backlog"
            )
            try:
                client.transition_issue(decision.task_id, target_state)
                client.comment_issue(
                    decision.task_id,
                    "Queue healing applied: "
                    f"{decision.transition.value} because {decision.reason} "
                    f"(lineage={decision.retry_lineage_id or 'unknown'}).",
                )
                entry["action"] = "transitioned"
            except Exception as exc:
                entry["action"] = "error"
                entry["error"] = str(exc)
        else:
            entry["action"] = "would_transition"
        queue_healing_actions.append(entry)

    client.close()
    print(json.dumps({
        "scanned_at": now.isoformat(),
        "apply":      args.apply,
        "rescore":    rescore_actions,
        "awaiting":   awaiting_actions,
        "queue_healing": queue_healing_actions,
    }, indent=2, ensure_ascii=False))
    return 0


def _labels(issue: dict[str, Any]) -> tuple[str, ...]:
    names: list[str] = []
    for raw in issue.get("labels", []) or []:
        if isinstance(raw, dict):
            name = raw.get("name")
        else:
            name = raw
        if name:
            names.append(str(name).strip())
    return tuple(names)


def _label_value(labels: tuple[str, ...], *prefixes: str) -> str | None:
    lowered = [(label.lower(), label) for label in labels]
    for prefix in prefixes:
        prefix_l = prefix.lower()
        for low, original in lowered:
            if low.startswith(prefix_l):
                return original[len(prefix):].strip()
    return None


def _state_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).strip()
    return str(state or "").strip()


def _parse_updated_at(issue: dict[str, Any]) -> datetime | None:
    raw = issue.get("updated_at") or issue.get("created_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_int(value: str | None) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _queue_task_from_issue(
    issue: dict[str, Any],
    *,
    duplicate_blocked_keys: set[str],
) -> tuple[QueueHealingTask, bool]:
    labels = _labels(issue)
    duplicate_key = _label_value(labels, "duplicate:", "dedup:")
    retry_lineage_id = _label_value(labels, "retry-lineage:", "lineage:")
    retry_safe = any(label.lower() in {"retry_safe", "retry-safe"} for label in labels)
    no_consumer = any(
        label.lower() in {"queue_deadlock", "queue-deadlock", "no_consumer", "no-consumer"}
        for label in labels
    )
    retry_count_raw = _label_value(labels, "retry-count:")
    recovery_count_raw = _label_value(labels, "recovery-attempts:")
    return (
        QueueHealingTask(
            task_id=str(issue["id"]),
            title=str(issue.get("name") or ""),
            state=_state_name(issue),
            duplicate_key=duplicate_key,
            duplicate_exists_in_blocked=(
                duplicate_key is not None and duplicate_key in duplicate_blocked_keys
            ),
            retry_safe=retry_safe,
            blocked_reason=_label_value(labels, "blocked-reason:"),
            blocked_by_backend=_label_value(labels, "blocked-by-backend:"),
            backend_dependency=_label_value(labels, "backend:"),
            retry_lineage_id=retry_lineage_id,
            retry_count=_parse_int(retry_count_raw),
            recovery_attempt_count=_parse_int(recovery_count_raw),
            updated_at=_parse_updated_at(issue),
            labels=labels,
        ),
        no_consumer,
    )


def _duplicate_blocked_keys(items: list[dict[str, Any]]) -> set[str]:
    counts: dict[str, int] = {}
    for issue in items:
        if _state_name(issue).lower() != "blocked":
            continue
        key = _label_value(_labels(issue), "duplicate:", "dedup:")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count > 1}


def _queue_healing_actions(items: list[dict[str, Any]], *, now: datetime):
    engine = QueueHealingEngine()
    duplicate_keys = _duplicate_blocked_keys(items)
    decisions = []
    for issue in items:
        if _state_name(issue).lower() != "blocked":
            continue
        task, no_consumer = _queue_task_from_issue(
            issue,
            duplicate_blocked_keys=duplicate_keys,
        )
        decision = engine.decide(task, no_consumer_can_execute=no_consumer, now=now)
        if decision.transition != QueueTransition.NONE or decision.escalate:
            decisions.append(decision)
    return decisions


if __name__ == "__main__":
    raise SystemExit(main())
