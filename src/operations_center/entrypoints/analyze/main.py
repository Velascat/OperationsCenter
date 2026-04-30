# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from operations_center.decision.models import ProposalCandidatesArtifact
from operations_center.proposer.result_models import ProposalResultsArtifact


_DECISION_ROOT = Path("tools/report/operations_center/decision")
_PROPOSER_ROOT = Path("tools/report/operations_center/proposer")

ALL_FAMILIES = frozenset([
    "observation_coverage",
    "test_visibility",
    "dependency_drift",
    "hotspot_concentration",
    "todo_accumulation",
])


def _load_decision_artifacts(root: Path, repo: str | None, limit: int) -> list[ProposalCandidatesArtifact]:
    paths = sorted(root.glob("*/proposal_candidates.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    artifacts = []
    for path in paths:
        try:
            a = ProposalCandidatesArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            if repo:
                norm = repo.strip().lower()
                if a.repo.name.strip().lower() != norm and str(a.repo.path).strip().lower() != norm:
                    continue
            artifacts.append(a)
        except Exception:
            continue
        if len(artifacts) >= limit:
            break
    return artifacts


def _load_proposer_artifacts(root: Path, limit: int) -> list[ProposalResultsArtifact]:
    paths = sorted(root.glob("*/proposal_results.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    artifacts = []
    for path in paths[:limit]:
        try:
            artifacts.append(ProposalResultsArtifact.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return artifacts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze decision + proposer artifacts to surface threshold tuning recommendations."
    )
    parser.add_argument("--repo", help="Filter by repo name or path key")
    parser.add_argument("--limit", type=int, default=20, help="Max number of decision runs to analyze (default: 20)")
    parser.add_argument(
        "--decision-root",
        default=str(_DECISION_ROOT),
        help=f"Path to decision artifact directory (default: {_DECISION_ROOT})",
    )
    parser.add_argument(
        "--proposer-root",
        default=str(_PROPOSER_ROOT),
        help=f"Path to proposer artifact directory (default: {_PROPOSER_ROOT})",
    )
    parser.add_argument("--json", dest="output_json", action="store_true", help="Output raw stats as JSON")
    args = parser.parse_args()

    decision_root = Path(args.decision_root)
    proposer_root = Path(args.proposer_root)

    if not decision_root.exists():
        print(f"No decision artifacts found at {decision_root}")
        return

    decisions = _load_decision_artifacts(decision_root, args.repo, args.limit)
    if not decisions:
        print("No decision artifacts matched the filter.")
        return

    proposer_artifacts = _load_proposer_artifacts(proposer_root, args.limit) if proposer_root.exists() else []
    # Index proposer results by source_decision_run_id
    proposer_by_decision: dict[str, ProposalResultsArtifact] = {}
    for pa in proposer_artifacts:
        proposer_by_decision[pa.source_decision_run_id] = pa

    # Aggregate per-family stats
    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    suppression_reasons: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    dry_run_count = 0
    live_run_count = 0

    for artifact in decisions:
        if artifact.dry_run:
            dry_run_count += 1
        else:
            live_run_count += 1

        for c in artifact.candidates:
            stats[c.family]["emitted"] += 1

        for s in artifact.suppressed:
            stats[s.family]["suppressed"] += 1
            suppression_reasons[s.family][s.reason] += 1

        # Cross-reference with proposer output
        prop = proposer_by_decision.get(artifact.run_id)
        if prop:
            for r in prop.created:
                stats[r.family]["created"] += 1
            for r in prop.skipped:
                stats[r.family]["skipped_by_guardrail"] += 1

    if args.output_json:
        output = {
            "runs_analyzed": len(decisions),
            "dry_run_count": dry_run_count,
            "live_run_count": live_run_count,
            "families": {
                family: dict(counts)
                for family, counts in sorted(stats.items())
            },
            "suppression_reasons": {
                family: dict(reasons)
                for family, reasons in sorted(suppression_reasons.items())
            },
        }
        print(json.dumps(output, indent=2))
        return

    # Human-readable report
    repo_label = args.repo or "all repos"
    print(f"Artifact Analysis Report  ({repo_label}, {len(decisions)} run(s))")
    print(f"  dry-run runs : {dry_run_count}")
    print(f"  live runs    : {live_run_count}")
    print()

    all_families = sorted(stats.keys() | ALL_FAMILIES)
    col_w = max(len(f) for f in all_families) + 2

    header = f"  {'family':<{col_w}}  {'emitted':>8}  {'suppressed':>10}  {'created':>8}  {'guardrail_skip':>14}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for family in all_families:
        s = stats.get(family, {})
        emitted = s.get("emitted", 0)
        suppressed = s.get("suppressed", 0)
        created = s.get("created", 0)
        guardrail = s.get("skipped_by_guardrail", 0)
        total = emitted + suppressed
        suppress_pct = f"({100 * suppressed // total}%)" if total else ""
        print(
            f"  {family:<{col_w}}  {emitted:>8}  {suppressed:>10} {suppress_pct:<6}  {created:>8}  {guardrail:>14}"
        )

    print()
    print("Suppression reasons:")
    if suppression_reasons:
        for family in sorted(suppression_reasons):
            for reason, count in sorted(suppression_reasons[family].items(), key=lambda x: -x[1]):
                print(f"  {family} / {reason}: {count}")
    else:
        print("  none recorded")

    print()
    print("Recommendations:")
    recommendations: list[str] = []

    for family in all_families:
        s = stats.get(family, {})
        emitted = s.get("emitted", 0)
        suppressed = s.get("suppressed", 0)
        total = emitted + suppressed
        if total == 0:
            if family in ("hotspot_concentration", "todo_accumulation"):
                recommendations.append(
                    f"  {family}: never evaluated — try --all-families on next autonomy-cycle run to see if it surfaces candidates"
                )
            else:
                recommendations.append(f"  {family}: no data yet — run more observe/decide cycles")
            continue
        suppress_rate = suppressed / total
        if suppress_rate >= 0.9 and total >= 3:
            reasons = suppression_reasons.get(family, {})
            top_reason = max(reasons, key=lambda r: reasons[r]) if reasons else "unknown"
            recommendations.append(
                f"  {family}: suppression rate {suppress_rate:.0%} ({suppressed}/{total}) — "
                f"top reason: {top_reason}. Consider loosening thresholds."
            )
        elif emitted > 0 and s.get("created", 0) == 0 and live_run_count > 0:
            recommendations.append(
                f"  {family}: {emitted} emitted but 0 created — all blocked by guardrails or max_create. "
                f"Check cooldown_minutes / max_create settings."
            )
        else:
            recommendations.append(f"  {family}: healthy ({emitted} emitted, {suppressed} suppressed)")

    for r in recommendations:
        print(r)


if __name__ == "__main__":
    main()
