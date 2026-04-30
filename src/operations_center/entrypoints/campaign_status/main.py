# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""S10-4: campaign-status CLI entrypoint.

Displays the progress of all multi-step campaigns tracked in
``state/campaigns.json``.  A campaign is created by ``build_multi_step_plan()``
in the worker whenever a complex task is decomposed into Analyze → Implement →
Verify steps.

Usage::

    python -m operations_center.entrypoints.campaign_status.main
    python -m operations_center.entrypoints.campaign_status.main --status in_progress
    python -m operations_center.entrypoints.campaign_status.main --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show progress for all multi-step execution campaigns."
    )
    parser.add_argument(
        "--status",
        choices=["in_progress", "completed", "partial", "cancelled"],
        default=None,
        help="Filter by campaign status (default: show all).",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output as JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--store",
        default=None,
        help="Path to campaigns.json (default: state/campaigns.json).",
    )
    args = parser.parse_args()

    from operations_center.execution.campaign_store import CampaignStore

    store_path = Path(args.store) if args.store else None
    store = CampaignStore(path=store_path) if store_path else CampaignStore()
    campaigns = store.list_campaigns(status=args.status)

    if args.output_json:
        print(json.dumps(campaigns, indent=2))
        return

    if not campaigns:
        filter_msg = f" with status={args.status!r}" if args.status else ""
        print(f"No campaigns found{filter_msg}.")
        return

    print(f"\n{'─' * 70}")
    print(f"  Campaign Status Report — {len(campaigns)} campaign(s)")
    print(f"{'─' * 70}\n")

    for c in campaigns:
        status_icon = {
            "completed": "✓",
            "in_progress": "…",
            "partial": "~",
            "cancelled": "✗",
        }.get(c.get("status", ""), "?")

        total = c.get("total_steps", 0)
        done = c.get("completed_steps", 0)
        pct = c.get("progress_pct", 0.0)
        bar_filled = int(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)

        print(f"  {status_icon}  {c.get('title', 'Untitled')[:60]}")
        print(f"     source_task_id: {c.get('source_task_id', '')}")
        print(f"     status:         {c.get('status', '')}")
        print(f"     progress:       [{bar}] {done}/{total} steps ({pct}%)")
        print(f"     created_at:     {c.get('created_at', '')[:19]}")
        print(f"     updated_at:     {c.get('updated_at', '')[:19]}")
        if c.get("done_step_ids"):
            print(f"     done_steps:     {', '.join(c['done_step_ids'])}")
        if c.get("cancelled_step_ids"):
            print(f"     cancelled:      {', '.join(c['cancelled_step_ids'])}")
        print()

    print(f"{'─' * 70}\n")


if __name__ == "__main__":
    main()
