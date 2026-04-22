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
            "Supported runtime is recommendation-only."
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
            "Deprecated. Supported runtime no longer auto-applies tuning changes; "
            "requested apply actions are recorded as skipped recommendations."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    os.environ.setdefault("CONTROL_PLANE_CONFIG", args.config)
    load_settings(args.config)  # validates config is readable
    report_root = Path("tools/report/control_plane")

    auto_apply = False
    if args.apply:
        print(
            "  [warn] --apply is deprecated. Supported runtime remains recommendation-only; "
            "apply candidates will be recorded as skipped."
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

    # S8-10: Confidence calibration report
    try:
        from control_plane.tuning.calibration import ConfidenceCalibrationStore, _MIN_SAMPLE_SIZE
        cal_records = ConfidenceCalibrationStore().report()
        if cal_records:
            print("\n  Confidence calibration:")
            print(f"  {'family':<28} {'conf':<8} {'n':>5} {'accept%':>9} {'expected':>9} {'ratio':>7}")
            for r in cal_records:
                flag = "⚠" if r.calibration_ratio < 0.6 else (" ✓" if r.calibration_ratio >= 0.9 else "  ")
                print(
                    f"  {r.family:<28} {r.confidence:<8} {r.total:>5} "
                    f"{r.acceptance_rate:>8.0%} {r.expected_rate:>8.0%} {r.calibration_ratio:>6.2f}{flag}"
                )
        else:
            print(f"\n  Confidence calibration: no data yet (need ≥{_MIN_SAMPLE_SIZE} records per family/confidence).")
    except Exception:
        pass


if __name__ == "__main__":
    main()
