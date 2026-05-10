# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Manage autonomy tier configuration for proposal candidate families.

Tiers control whether proposals created by the autonomy cycle are placed in
'Backlog' (tier 1, human must promote) or 'Ready for AI' (tier 2, auto-execute).

Usage:
    # Show current tier configuration
    python -m operations_center.entrypoints.autonomy_tiers.main show

    # Promote a family to tier 2 (auto-execute)
    python -m operations_center.entrypoints.autonomy_tiers.main promote --family lint_fix

    # Demote a family back to tier 1 (human gate)
    python -m operations_center.entrypoints.autonomy_tiers.main demote --family lint_fix

    # Set a specific tier
    python -m operations_center.entrypoints.autonomy_tiers.main set --family observation_coverage --tier 2 --note "10 accepted proposals, 0% escalation"
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime

from operations_center.autonomy_tiers.config import (
    AutonomyTiersConfig,
    _DEFAULT_FAMILY_TIERS,
    get_family_tier,
    load_tiers_config,
    save_tiers_config,
)

_ALL_KNOWN_FAMILIES = sorted(_DEFAULT_FAMILY_TIERS.keys())


def cmd_show(_args: argparse.Namespace) -> None:
    config = load_tiers_config()
    print("Autonomy Tier Configuration")
    print("=" * 50)
    if config:
        print("  config file:  config/autonomy_tiers.json")
        print(f"  updated_at:   {config.updated_at.isoformat()}")
        print(f"  version:      {config.version}")
    else:
        print("  config file:  not present (using defaults)")
    print()
    print(f"  {'Family':<35} {'Tier':<6} {'Source':<12} {'Note'}")
    print(f"  {'-'*35} {'-'*6} {'-'*12} {'-'*30}")
    for family in _ALL_KNOWN_FAMILIES:
        tier = get_family_tier(family, config)
        source = "override" if (config and family in config.overrides) else "default"
        note = (config.notes.get(family, "") if config else "")
        tier_label = {0: "0 (disabled)", 1: "1 (backlog)", 2: "2 (auto-run)"}.get(tier, str(tier))
        print(f"  {family:<35} {tier_label:<18} {source:<12} {note}")


def cmd_promote(args: argparse.Namespace) -> None:
    family = args.family
    config = load_tiers_config()
    current_tier = get_family_tier(family, config)
    new_tier = current_tier + 1
    if new_tier > 2:
        print(f"  {family} is already at maximum tier ({current_tier}). No change.")
        return
    _apply_tier(family, new_tier, note=args.note, reason=f"promoted from tier {current_tier}")


def cmd_demote(args: argparse.Namespace) -> None:
    family = args.family
    config = load_tiers_config()
    current_tier = get_family_tier(family, config)
    new_tier = max(0, current_tier - 1)
    if new_tier == current_tier:
        print(f"  {family} is already at minimum tier (0). No change.")
        return
    _apply_tier(family, new_tier, note=args.note, reason=f"demoted from tier {current_tier}")


def cmd_set(args: argparse.Namespace) -> None:
    tier = args.tier
    if tier not in (0, 1, 2):
        print(f"  Error: tier must be 0, 1, or 2 (got {tier})")
        return
    _apply_tier(args.family, tier, note=args.note, reason=f"explicit set to tier {tier}")


def _apply_tier(family: str, tier: int, *, note: str | None, reason: str) -> None:
    config = load_tiers_config() or AutonomyTiersConfig(
        updated_at=datetime.now(UTC),
        overrides={},
    )
    overrides = dict(config.overrides)
    notes = dict(config.notes)
    overrides[family] = tier
    if note:
        notes[family] = note
    updated = AutonomyTiersConfig(
        version=config.version,
        updated_at=datetime.now(UTC),
        overrides=overrides,
        notes=notes,
    )
    save_tiers_config(updated)
    tier_label = {0: "disabled (tier 0)", 1: "Backlog (tier 1)", 2: "Ready for AI (tier 2)"}.get(tier, str(tier))
    print(f"  {family}: {reason} → {tier_label}")
    if note:
        print(f"  note: {note}")
    print("  Saved to config/autonomy_tiers.json")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage autonomy tier configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("show", help="Show current tier configuration")

    promote_p = subparsers.add_parser("promote", help="Promote a family one tier (1→2)")
    promote_p.add_argument("--family", required=True, choices=_ALL_KNOWN_FAMILIES)
    promote_p.add_argument("--note", help="Reason for promotion (stored in config)")

    demote_p = subparsers.add_parser("demote", help="Demote a family one tier (2→1 or 1→0)")
    demote_p.add_argument("--family", required=True, choices=_ALL_KNOWN_FAMILIES)
    demote_p.add_argument("--note", help="Reason for demotion (stored in config)")

    set_p = subparsers.add_parser("set", help="Set a family to a specific tier (0, 1, or 2)")
    set_p.add_argument("--family", required=True, choices=_ALL_KNOWN_FAMILIES)
    set_p.add_argument("--tier", required=True, type=int, choices=[0, 1, 2])
    set_p.add_argument("--note", help="Reason (stored in config)")

    args = parser.parse_args()
    if args.command == "show":
        cmd_show(args)
    elif args.command == "promote":
        cmd_promote(args)
    elif args.command == "demote":
        cmd_demote(args)
    elif args.command == "set":
        cmd_set(args)


if __name__ == "__main__":
    main()
