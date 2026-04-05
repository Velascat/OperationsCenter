from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from control_plane.config import load_settings
from control_plane.tuning.service import TuningRegulatorService, new_tuning_context


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded self-tuning regulation loop. "
            "Reads retained autonomy artifacts, computes per-family behavior metrics, "
            "and emits conservative tuning recommendations. "
            "Recommendation-only by default — pass --apply to enable bounded auto-apply."
        )
    )
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--window",
        type=int,
        default=20,
        help="Number of most-recent decision artifact runs to include in the analysis window (default: 20).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Enable bounded auto-apply mode. "
            "Applies conservative threshold changes within strict guardrails. "
            "Writes to config/autonomy_tuning.json. "
            "Requires CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1 as an additional safety check."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    os.environ.setdefault("CONTROL_PLANE_CONFIG", args.config)
    load_settings(args.config)  # validates config is readable
    report_root = Path("tools/report/control_plane")

    auto_apply = args.apply and os.environ.get("CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED") == "1"
    if args.apply and not auto_apply:
        print(
            "  [warn] --apply passed but CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1 is not set. "
            "Running in recommendation-only mode."
        )

    context = new_tuning_context(
        decision_root=report_root / "decision",
        proposer_root=report_root / "proposer",
        auto_apply=auto_apply,
        window=args.window,
        source_command="control-plane tune-autonomy",
    )

    service = TuningRegulatorService()
    artifact, paths = service.run(context)

    print(f"[tune-autonomy] window={artifact.window_runs} runs  auto_apply={artifact.auto_apply}")

    if artifact.family_metrics:
        print("\n  Family metrics:")
        print(f"  {'family':<30} {'emitted':>8} {'suppressed':>10} {'created':>8} {'sup%':>6} {'create%':>8}")
        for m in artifact.family_metrics:
            print(
                f"  {m.family:<30} {m.candidates_emitted:>8} {m.candidates_suppressed:>10} "
                f"{m.candidates_created:>8} {m.suppression_rate:>6.0%} {m.create_rate:>8.0%}"
            )
    else:
        print("\n  No family metrics (no retained decision artifacts found).")

    if artifact.recommendations:
        print("\n  Recommendations:")
        for r in artifact.recommendations:
            marker = "✓" if r.action == "keep" else "→"
            print(f"  {marker} [{r.family}] {r.action}  ({r.confidence})  — {r.rationale[:80]}")

    if artifact.changes_applied:
        print("\n  Applied changes:")
        for c in artifact.changes_applied:
            print(f"  • [{c.family}] {c.key}: {c.before} → {c.after}  ({c.reason[:60]})")

    if artifact.changes_skipped:
        print("\n  Skipped changes:")
        for s in artifact.changes_skipped:
            print(f"  ✗ [{s.family}] {s.intended_action}: {s.reason}")

    if paths:
        print(f"\n  Artifacts: {paths[0]}")
    else:
        print("\n  No artifacts written (empty result).")


if __name__ == "__main__":
    main()
