# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Recover Plane tasks stuck in `Running` state.

A worker can die mid-task (OOM, restart, kodo subprocess hang) and leave
Plane showing "Running" indefinitely. The ghost-work audit detects these as
G8 but doesn't reclaim them. This entrypoint resets stale Running tasks to
Ready for AI with an audit comment so they can be re-claimed.

Run periodically (e.g. via cron or watchdog hook):

    python -m operations_center.entrypoints.maintenance.recover_stale \\
        --config config/operations_center.local.yaml \\
        [--max-age-seconds 14400]   # default: 4 hours
        [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings


_DEFAULT_MAX_AGE = 4 * 60 * 60  # 4 hours


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset stale Running tasks to Ready for AI")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--max-age-seconds", type=int, default=_DEFAULT_MAX_AGE,
                        help=f"reset Running tasks older than this (default: {_DEFAULT_MAX_AGE}s = 4h)")
    parser.add_argument(
        "--per-kind", action="store_true",
        help="use per-task-kind TTLs (goal=4h, test=45min, improve=90min) "
             "instead of a single threshold. Honors task-kind labels.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be reset without modifying Plane")
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
        print(json.dumps({"error": f"plane_fetch_failed: {exc}"}))
        return 1

    now = datetime.now(UTC)
    cutoff_seconds = args.max_age_seconds
    reset: list[dict] = []
    skipped: list[dict] = []

    # Per-kind variant uses the helper from operations_center.reconcile_running
    # which has its own TTL map (goal=4h, test=45min, etc.) and returns a
    # structured candidate list. We unify the output shape below.
    if args.per_kind:
        from operations_center.reconcile_running import reconcile_stale_running_issues
        candidates = reconcile_stale_running_issues(items, now=now)
        for cand in candidates:
            entry = {
                "id":         cand.task_id,
                "title":      cand.title,
                "task_kind":  cand.task_kind,
                "age_minutes": cand.age_minutes,
                "ttl_minutes": cand.ttl_minutes,
            }
            if args.dry_run:
                entry["action"] = "would_reset"
                reset.append(entry)
                continue
            try:
                client.transition_issue(cand.task_id, "Ready for AI")
                client.comment_issue(
                    cand.task_id,
                    f"Auto-recovered: per-kind TTL exceeded "
                    f"(kind={cand.task_kind}, age={cand.age_minutes}m, "
                    f"ttl={cand.ttl_minutes}m). Re-promoted to Ready for AI.",
                )
                entry["action"] = "reset"
                reset.append(entry)
            except Exception as exc:
                entry["action"] = "error"
                entry["error"] = str(exc)
                skipped.append(entry)
        client.close()
        out = {
            "scanned_at":      now.isoformat(),
            "mode":            "per-kind",
            "dry_run":         args.dry_run,
            "reset_count":     sum(1 for r in reset if r.get("action") == "reset"),
            "would_reset_count": sum(1 for r in reset if r.get("action") == "would_reset"),
            "skipped_count":   len(skipped),
            "reset":           reset,
            "skipped":         skipped,
        }
        print(json.dumps(out, indent=2))
        return 0

    for issue in items:
        state = issue.get("state")
        state_name = (state.get("name", "") if isinstance(state, dict) else str(state or "")).strip()
        if state_name.lower() != "running":
            continue
        updated = _parse_iso(issue.get("updated_at")) or _parse_iso(issue.get("created_at"))
        if updated is None:
            skipped.append({"id": str(issue.get("id")), "reason": "no_timestamp"})
            continue
        age = (now - updated).total_seconds()
        if age < cutoff_seconds:
            continue
        entry = {
            "id":    str(issue["id"]),
            "title": (issue.get("name") or "")[:80],
            "age_seconds": int(age),
        }
        if args.dry_run:
            entry["action"] = "would_reset"
            reset.append(entry)
            continue
        try:
            client.transition_issue(entry["id"], "Ready for AI")
            client.comment_issue(
                entry["id"],
                f"Auto-recovered from stale Running state by maintenance.recover_stale "
                f"(age {int(age/60)}m, threshold {int(cutoff_seconds/60)}m). "
                f"The previous run did not produce a result file — re-promoted to "
                f"Ready for AI for a fresh attempt.",
            )
            entry["action"] = "reset"
            reset.append(entry)
        except Exception as exc:
            entry["action"] = "error"
            entry["error"]  = str(exc)
            skipped.append(entry)

    client.close()
    out = {
        "scanned_at":     now.isoformat(),
        "max_age_seconds": cutoff_seconds,
        "dry_run":         args.dry_run,
        "reset_count":     sum(1 for r in reset if r.get("action") == "reset"),
        "would_reset_count": sum(1 for r in reset if r.get("action") == "would_reset"),
        "skipped_count":   len(skipped),
        "reset":           reset,
        "skipped":         skipped,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
