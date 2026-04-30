# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
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

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.priority_scans import (
    handle_awaiting_input_scan,
    handle_priority_rescore_scan,
)


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
        print(json.dumps({"error": f"plane_fetch_failed: {exc}"}))
        return 1

    now = datetime.now(UTC)
    rescore_candidates = handle_priority_rescore_scan(items, now=now)
    awaiting = handle_awaiting_input_scan(items, client, state_name=args.awaiting_input_state)

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
                    f"re-promoted to Ready for AI for another kodo pass.",
                )
                entry["action"] = "transitioned"
            except Exception as exc:
                entry["action"] = "error"
                entry["error"] = str(exc)
        else:
            entry["action"] = "would_transition"
        awaiting_actions.append(entry)

    client.close()
    print(json.dumps({
        "scanned_at": now.isoformat(),
        "apply":      args.apply,
        "rescore":    rescore_actions,
        "awaiting":   awaiting_actions,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
