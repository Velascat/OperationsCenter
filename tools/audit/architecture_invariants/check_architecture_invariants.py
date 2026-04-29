# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""CLI entrypoint for the OperationsCenter architecture invariant audit.

Usage:
    python -m tools.audit.architecture_invariants.check_architecture_invariants
    python -m tools.audit.architecture_invariants.check_architecture_invariants \\
        --repo-root . \\
        --json-out tools/audit/report/architecture_invariants/latest.json \\
        --summary-out tools/audit/report/architecture_invariants/latest.md

Rules checked:
  OC-ARCH-IMPORT   — no managed repo imports in src/operations_center/
  OC-ARCH-LAYER    — unidirectional import graph (dispatch/governance isolation)
  OC-ARCH-SCAN     — no directory scanning in artifact_index/
  OC-ARCH-COLLAPSE — anti-collapse guardrail structurally present and non-empty
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.audit.architecture_invariants.baseline import (
    BaselineComparison,
    compare_to_baseline,
    load_baseline,
    save_baseline,
)
from tools.audit.architecture_invariants.import_rules import check_managed_repo_imports
from tools.audit.architecture_invariants.invariant_models import AuditReport, Status
from tools.audit.architecture_invariants.layer_rules import check_layer_direction
from tools.audit.architecture_invariants.mutation_rules import check_anti_collapse_guardrail
from tools.audit.architecture_invariants.scanning_rules import check_no_directory_scanning


def run_audit(repo_root: Path) -> AuditReport:
    findings = []
    findings.extend(check_managed_repo_imports(repo_root))
    findings.extend(check_layer_direction(repo_root))
    findings.extend(check_no_directory_scanning(repo_root))
    findings.extend(check_anti_collapse_guardrail(repo_root))
    return AuditReport(repo_root=str(repo_root), findings=findings)


def _render_markdown(report: AuditReport) -> str:
    counts = report.summary_counts()
    verdict_emoji = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(report.overall_status(), "")
    lines = [
        "# OperationsCenter Architecture Invariant Report",
        "",
        "## Verdict",
        "",
        f"{verdict_emoji} **{report.overall_status().upper()}**",
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|--------|-------|",
        f"| fail | {counts.get('fail', 0)} |",
        f"| warn | {counts.get('warn', 0)} |",
        f"| pass | {counts.get('pass', 0)} |",
        "",
    ]

    fail_findings = [f for f in report.findings if f.status == Status.FAIL]
    if fail_findings:
        lines += ["## Failing Invariants", ""]
        for f in fail_findings:
            lines += [
                f"### `{f.id}` — {f.family}",
                f"**File:** `{f.path}` line {f.line}",
                f"**Message:** {f.message}",
                f"**Evidence:** `{f.evidence}`",
                f"**Fix:** {f.suggested_fix}",
                "",
            ]
    else:
        lines += [
            "## Findings",
            "",
            "No invariant violations detected.",
            "",
        ]

    warn_findings = [f for f in report.findings if f.status == Status.WARN]
    if warn_findings:
        lines += ["## Warnings", ""]
        for f in warn_findings:
            lines.append(f"- `{f.id}` `{f.path}:{f.line}` — {f.message}")
        lines.append("")

    lines += [
        "---",
        "",
        "**Rules checked:**",
        "- `OC-ARCH-IMPORT` — no managed repo imports in `src/operations_center/`",
        "- `OC-ARCH-LAYER` — unidirectional import graph (dispatch/governance isolation)",
        "- `OC-ARCH-SCAN` — no directory scanning in `artifact_index/`",
        "- `OC-ARCH-COLLAPSE` — anti-collapse guardrail structurally present and non-empty",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OperationsCenter architecture invariant audit"
    )
    parser.add_argument("--repo-root", default=".", help="Path to repo root (default: .)")
    parser.add_argument("--json-out", default=None, help="Write JSON report to this path")
    parser.add_argument("--summary-out", default=None, help="Write Markdown report to this path")
    parser.add_argument(
        "--baseline", default=None,
        help="Path to baseline JSON. New findings since baseline cause non-zero exit.",
    )
    parser.add_argument(
        "--capture-baseline", default=None,
        help="Capture current findings as a new baseline JSON at this path.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    report = run_audit(repo_root)

    md_str = _render_markdown(report)
    json_str = report.to_json()

    print(md_str)

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json_str, encoding="utf-8")
        print(f"\nJSON report written to {out}", file=sys.stderr)

    if args.summary_out:
        out = Path(args.summary_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md_str, encoding="utf-8")
        print(f"Markdown report written to {out}", file=sys.stderr)

    if args.capture_baseline:
        out = Path(args.capture_baseline)
        save_baseline([f.to_dict() for f in report.findings], out)
        print(f"Baseline captured to {out}", file=sys.stderr)

    if args.baseline:
        baseline_findings = load_baseline(Path(args.baseline))
        current_dicts = [f.to_dict() for f in report.findings]
        result: BaselineComparison = compare_to_baseline(baseline_findings, current_dicts)

        if result.resolved_count:
            print(f"\n✅ RESOLVED ({result.resolved_count} findings fixed since baseline):")
            for f in result.resolved_findings:
                print(f"  {f['path']}:{f['line']} [{f['family']}] {f['evidence']}")

        if result.new_count:
            print(f"\n❌ NEW ({result.new_count} new findings since baseline):")
            for f in result.new_findings:
                print(f"  {f['path']}:{f['line']} [{f['family']}] {f['message']}")
            return 1

        print(
            f"\n✅ No new findings vs baseline "
            f"({result.existing_count} existing, {result.resolved_count} resolved)."
        )
        return 0

    return 1 if report.overall_status() == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
