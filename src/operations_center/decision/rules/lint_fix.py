# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class LintFixRule:
    """Turn lint_drift insights into fix candidates.

    Fires on:
    - lint_violations_present with enough violations to warrant a fix task.
    - lint_violations_worsened (regression since last snapshot).
    """

    def __init__(self, *, min_violations: int = 5) -> None:
        self.min_violations = min_violations

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        # Cross-signal: check once for lint↔hotspot overlap across all insights.
        has_hotspot_overlap = any(
            i.kind == "cross_signal" and i.subject == "lint_hotspot_overlap"
            for i in insights
        )

        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "lint_drift":
                continue

            if (
                insight.dedup_key.endswith("present")
                and insight.status == "present"
            ):
                count = int(insight.evidence.get("violation_count", 0))
                if count < self.min_violations:
                    continue
                top_codes = insight.evidence.get("top_codes", [])
                codes_str = ", ".join(str(c) for c in top_codes[:3]) if top_codes else "various"
                distinct_files = insight.evidence.get("distinct_file_count")
                # Confidence is high when count is large OR when violations overlap with
                # git hotspots (corroborating evidence from a second signal).
                confidence = "high" if (count >= 20 or has_hotspot_overlap) else "medium"
                ev_lines = [f"ruff reports {count} lint violation(s). Top codes: {codes_str}."]
                if has_hotspot_overlap:
                    ev_lines.append("Lint violations overlap with active git hotspot files (cross-signal corroboration).")
                matched = ["lint_violations_present_min_threshold", "candidate_not_seen_in_cooldown_window"]
                if has_hotspot_overlap:
                    matched.append("cross_signal_lint_hotspot_overlap")
                candidates.append(
                    CandidateSpec(
                        family="lint_fix",
                        subject="lint_violations",
                        pattern_key="violations_present",
                        evidence=dict(insight.evidence),
                        matched_rules=matched,
                        confidence=confidence,
                        evidence_lines=ev_lines,
                        risk_class="style",
                        expires_after_runs=3,
                        estimated_affected_files=int(distinct_files) if distinct_files is not None else None,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Fix {count} ruff lint violation(s) ({codes_str})",
                            summary_hint=(
                                f"ruff check found {count} lint violation(s) in the codebase. "
                                f"Top violation codes: {codes_str}. "
                                "Run `ruff check --fix` to auto-fix where possible, then manually resolve "
                                "any remaining violations. Keep the change scoped to lint fixes only."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="lint_fix",
                        ),
                        priority=(1, 5, f"lint_fix|violations_present|{count}"),
                    )
                )

            elif insight.dedup_key.endswith("worsened"):
                delta = int(insight.evidence.get("delta", 0))
                current = int(insight.evidence.get("current_count", 0))
                distinct_files = insight.evidence.get("distinct_file_count")
                candidates.append(
                    CandidateSpec(
                        family="lint_fix",
                        subject="lint_violations",
                        pattern_key="violations_worsened",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "lint_violations_count_increased",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="high",
                        evidence_lines=[
                            f"Lint violations increased by {delta} (now {current} total).",
                        ],
                        risk_class="style",
                        expires_after_runs=3,
                        estimated_affected_files=int(distinct_files) if distinct_files is not None else None,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Fix lint regression: +{delta} new ruff violations",
                            summary_hint=(
                                f"Lint violation count increased by {delta} since the last observer snapshot "
                                f"(now {current} total). "
                                "Identify the recently added violations and fix them before they accumulate further."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="lint_fix",
                        ),
                        priority=(1, 4, f"lint_fix|violations_worsened|{delta}"),
                    )
                )

        return candidates
