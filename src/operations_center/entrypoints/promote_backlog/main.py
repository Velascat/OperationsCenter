# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Promote autonomy-created Plane tasks from Backlog → Ready for AI.

Finds tasks with label "source: autonomy" in Backlog state whose family's
current autonomy tier is >= 2, and moves them to "Ready for AI".

This handles the common case where the operator raises a family's tier
(via `autonomy-tiers set`) after tasks have already been created in Backlog.

Usage:
    # Dry-run (default): show what would be promoted, no Plane writes
    python -m operations_center.entrypoints.promote_backlog.main --config FILE

    # Execute: actually transition tasks
    python -m operations_center.entrypoints.promote_backlog.main --config FILE --execute

    # Limit to one family
    python -m operations_center.entrypoints.promote_backlog.main --config FILE --family lint_fix --execute
"""
from __future__ import annotations

import argparse

from operations_center.adapters.plane import PlaneClient
from operations_center.autonomy_tiers.config import get_family_tier, load_tiers_config
from operations_center.config import load_settings
from operations_center.proposer.backlog_promoter import BacklogPromoterService


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Promote autonomy-created Backlog tasks to Ready for AI "
            "when their family tier has been raised to 2."
        )
    )
    parser.add_argument("--config", required=True, help="Path to operations_center config YAML")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually transition tasks. Without this flag, runs dry-run only.",
    )
    parser.add_argument(
        "--family",
        default=None,
        help="Limit promotion to a specific candidate family.",
    )
    args = parser.parse_args()

    dry_run = not args.execute

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )

    tiers_config = load_tiers_config()

    def get_tier(family: str) -> int:
        return get_family_tier(family, tiers_config)

    service = BacklogPromoterService(
        plane_client=client,
        get_tier=get_tier,
        dry_run=dry_run,
    )

    try:
        result = service.promote(family_filter=args.family)
    finally:
        client.close()

    tag = "[dry-run] " if dry_run else ""

    if result.promoted:
        print(f"\n{tag}Promoted {result.promote_count} task(s) → Ready for AI:")
        for t in result.promoted:
            tier_note = f"  tier {t.recorded_tier}→{t.current_tier}" if t.recorded_tier and t.recorded_tier != t.current_tier else f"  tier {t.current_tier}"
            print(f"  {t.task_id}  [{t.family}]{tier_note}  {t.title}")
    else:
        print(f"\n{tag}No tasks to promote.")

    if result.skipped:
        skip_by_reason: dict[str, int] = {}
        for s in result.skipped:
            skip_by_reason[s.reason] = skip_by_reason.get(s.reason, 0) + 1
        print(f"\nSkipped {len(result.skipped)} task(s):")
        for reason, count in sorted(skip_by_reason.items()):
            print(f"  {reason}: {count}")

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  {err}")

    if dry_run and result.promoted:
        print("\nRun with --execute to apply.")


if __name__ == "__main__":
    main()
