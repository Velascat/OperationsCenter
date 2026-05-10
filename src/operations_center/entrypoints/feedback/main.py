# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Record proposal feedback outcomes for the autonomy feedback loop.

Also updates the Phase 6 confidence calibration store when the feedback record
includes a ``confidence`` field (set when recording outcomes for autonomy proposals
that carry a known confidence label).

The reviewer watcher writes feedback records automatically when it merges or
escalates a PR. This entrypoint handles the cases that fall outside the watcher:

  - A task branch was merged manually (not via the reviewer loop)
  - An operator wants to record an outcome retroactively
  - A task was abandoned or blocked without a PR
  - Testing or seeding the feedback system

Usage:
    # Record a merge outcome for a task
    python -m operations_center.entrypoints.feedback.main record \\
        --task-id <uuid> --outcome merged

    # Record an escalation (human took over the review)
    python -m operations_center.entrypoints.feedback.main record \\
        --task-id <uuid> --outcome escalated --pr-number 42

    # Record abandonment (task was closed without execution)
    python -m operations_center.entrypoints.feedback.main record \\
        --task-id <uuid> --outcome abandoned

    # List recorded feedback
    python -m operations_center.entrypoints.feedback.main list

    # Show feedback for a specific task
    python -m operations_center.entrypoints.feedback.main show --task-id <uuid>
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

PROPOSAL_FEEDBACK_DIR = Path("state/proposal_feedback")

_VALID_OUTCOMES = ("merged", "escalated", "abandoned", "blocked")


def cmd_record(args: argparse.Namespace) -> None:
    task_id = args.task_id
    outcome = args.outcome
    if outcome not in _VALID_OUTCOMES:
        print(f"  Error: outcome must be one of {_VALID_OUTCOMES} (got '{outcome}')")
        return

    feedback_path = PROPOSAL_FEEDBACK_DIR / f"{task_id}.json"
    if feedback_path.exists() and not args.force:
        existing = json.loads(feedback_path.read_text(encoding="utf-8"))
        print(
            f"  Feedback already exists for task {task_id}:\n"
            f"    outcome: {existing.get('outcome')}  recorded_at: {existing.get('recorded_at')}\n"
            "  Use --force to overwrite."
        )
        return

    PROPOSAL_FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    record: dict = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "task_id": task_id,
        "outcome": outcome,
        "source": "manual",
    }
    if args.pr_number is not None:
        record["pr_number"] = args.pr_number
    if args.note:
        record["note"] = args.note

    feedback_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Recorded: task={task_id}  outcome={outcome}  → {feedback_path}")

    # S8-10: Update confidence calibration store if confidence label is provided.
    if args.confidence and args.family:
        try:
            from operations_center.tuning.calibration import ConfidenceCalibrationStore
            cal = ConfidenceCalibrationStore()
            cal.record(args.family, args.confidence, outcome)
            print(f"  Calibration updated: family={args.family}  confidence={args.confidence}  outcome={outcome}")
        except Exception:
            pass  # calibration is best-effort


def cmd_list(args: argparse.Namespace) -> None:
    if not PROPOSAL_FEEDBACK_DIR.exists():
        print("  No feedback records found (state/proposal_feedback/ does not exist).")
        return

    records = []
    for path in sorted(PROPOSAL_FEEDBACK_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append(data)
        except Exception:
            continue

    if not records:
        print("  No feedback records found.")
        return

    # Sort by recorded_at descending
    records.sort(key=lambda r: r.get("recorded_at", ""), reverse=True)

    limit = getattr(args, "limit", 20)
    print(f"  Feedback records ({len(records)} total, showing {min(limit, len(records))}):")
    print(f"  {'task_id':<38} {'outcome':<12} {'source':<10} recorded_at")
    print(f"  {'-'*38} {'-'*12} {'-'*10} {'-'*24}")
    for r in records[:limit]:
        print(
            f"  {r.get('task_id', ''):<38} {r.get('outcome', ''):<12} "
            f"{r.get('source', ''):<10} {r.get('recorded_at', '')[:19]}"
        )

    outcome_counts: dict[str, int] = {}
    for r in records:
        o = r.get("outcome", "unknown")
        outcome_counts[o] = outcome_counts.get(o, 0) + 1
    print()
    for outcome, count in sorted(outcome_counts.items()):
        print(f"  {outcome}: {count}")


def cmd_show(args: argparse.Namespace) -> None:
    path = PROPOSAL_FEEDBACK_DIR / f"{args.task_id}.json"
    if not path.exists():
        print(f"  No feedback record found for task {args.task_id}")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  Error reading feedback record: {exc}")
        return
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record and inspect proposal feedback outcomes for the autonomy feedback loop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_p = subparsers.add_parser(
        "record",
        help=f"Record a feedback outcome for a task ({', '.join(_VALID_OUTCOMES)})",
    )
    record_p.add_argument("--task-id", required=True, help="Plane task UUID")
    record_p.add_argument(
        "--outcome",
        required=True,
        choices=list(_VALID_OUTCOMES),
        help="Outcome to record",
    )
    record_p.add_argument("--pr-number", type=int, help="GitHub PR number (optional)")
    record_p.add_argument("--note", help="Free-text note (stored in record)")
    record_p.add_argument(
        "--force", action="store_true", help="Overwrite an existing feedback record"
    )
    # S8-10: Optional confidence calibration fields
    record_p.add_argument("--family", help="Proposal family (e.g. lint_fix) for calibration tracking")
    record_p.add_argument("--confidence", choices=["high", "medium", "low"],
                          help="Confidence label assigned at proposal time, for calibration")

    list_p = subparsers.add_parser("list", help="List all feedback records")
    list_p.add_argument("--limit", type=int, default=20, help="Max records to display (default: 20)")

    show_p = subparsers.add_parser("show", help="Show feedback record for a specific task")
    show_p.add_argument("--task-id", required=True, help="Plane task UUID")

    args = parser.parse_args()
    if args.command == "record":
        cmd_record(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)


if __name__ == "__main__":
    main()
