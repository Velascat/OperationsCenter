# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Cancel autonomy Backlog tasks older than Settings.stale_autonomy_backlog_days.

Wires the previously-dead `stale_autonomy_backlog_days` field. Backlog
tasks that have sat unpromoted for longer than the threshold are
transitioned to ``Cancelled`` with an audit comment. Only autonomy-
sourced tasks (label ``source: autonomy``) are touched — operator-
created Backlog items are left alone.

Default threshold: 30 days. Conservative — adjust per-deployment via
``stale_autonomy_backlog_days`` in config.

    python -m operations_center.entrypoints.maintenance.cleanup_stale_backlog \\
        --config config/operations_center.local.yaml [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Cancel stale autonomy Backlog tasks")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings = load_settings(args.config)
    threshold_days = int(getattr(settings, "stale_autonomy_backlog_days", 30) or 30)
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
    cancelled: list[dict] = []
    skipped: list[dict] = []

    for issue in items:
        st = issue.get("state")
        st_name = (st.get("name", "") if isinstance(st, dict) else str(st or "")).strip().lower()
        if st_name != "backlog":
            continue
        labels = [
            (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            for lab in issue.get("labels", []) or []
        ]
        if "source: autonomy" not in labels:
            continue  # operator-sourced backlog — leave alone
        # Skip lifecycle: archived (already terminal-frozen)
        if "lifecycle: archived" in labels:
            continue
        ts = _parse_iso(issue.get("updated_at")) or _parse_iso(issue.get("created_at"))
        if ts is None:
            continue
        age_days = (now - ts).total_seconds() / 86400
        if age_days < threshold_days:
            continue
        entry = {
            "id":        str(issue.get("id")),
            "title":     (issue.get("name") or "")[:80],
            "age_days":  round(age_days, 1),
            "threshold": threshold_days,
        }
        if args.dry_run:
            entry["action"] = "would_cancel"
            cancelled.append(entry)
            continue
        try:
            client.transition_issue(entry["id"], "Cancelled")
            client.comment_issue(
                entry["id"],
                f"Auto-cancelled: autonomy Backlog item has been stale "
                f"{round(age_days, 1)}d (threshold {threshold_days}d, from "
                f"Settings.stale_autonomy_backlog_days). Re-open if still relevant.",
            )
            entry["action"] = "cancelled"
            cancelled.append(entry)
        except Exception as exc:
            entry["action"] = "error"
            entry["error"]  = str(exc)
            skipped.append(entry)

    client.close()
    out = {
        "scanned_at":          now.isoformat(),
        "dry_run":             args.dry_run,
        "threshold_days":      threshold_days,
        "cancelled_count":     sum(1 for c in cancelled if c.get("action") == "cancelled"),
        "would_cancel_count":  sum(1 for c in cancelled if c.get("action") == "would_cancel"),
        "skipped_count":       len(skipped),
        "cancelled":           cancelled,
        "skipped":             skipped[:20],
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
