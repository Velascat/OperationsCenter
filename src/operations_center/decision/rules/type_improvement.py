# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class TypeImprovementRule:
    """Turn type_health insights into fix candidates.

    Fires on:
    - type_errors_present with enough errors to warrant a fix task.
    - type_errors_worsened (regression since last snapshot).
    """

    def __init__(self, *, min_errors: int = 3) -> None:
        self.min_errors = min_errors

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        # Cross-signal: check once for type↔hotspot overlap across all insights.
        has_hotspot_overlap = any(
            i.kind == "cross_signal" and i.subject == "type_hotspot_overlap"
            for i in insights
        )

        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "type_health":
                continue

            if insight.dedup_key.endswith("present") and insight.status == "present":
                count = int(insight.evidence.get("error_count", 0))
                if count < self.min_errors:
                    continue
                source = insight.evidence.get("source", "type checker")
                top_codes = insight.evidence.get("top_codes", [])
                codes_str = ", ".join(str(c) for c in top_codes[:3]) if top_codes else "various"
                distinct_files = insight.evidence.get("distinct_file_count")
                confidence = "high" if (count >= 10 or has_hotspot_overlap) else "medium"
                ev_lines = [f"{source} reports {count} type error(s). Top codes: {codes_str}."]
                if has_hotspot_overlap:
                    ev_lines.append("Type errors overlap with active git hotspot files (cross-signal corroboration).")
                matched = ["type_errors_present_min_threshold", "candidate_not_seen_in_cooldown_window"]
                if has_hotspot_overlap:
                    matched.append("cross_signal_type_hotspot_overlap")
                candidates.append(
                    CandidateSpec(
                        family="type_fix",
                        subject="type_errors",
                        pattern_key="errors_present",
                        evidence=dict(insight.evidence),
                        matched_rules=matched,
                        confidence=confidence,
                        evidence_lines=ev_lines,
                        risk_class="logic",
                        expires_after_runs=4,
                        estimated_affected_files=int(distinct_files) if distinct_files is not None else None,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Fix {count} type error(s) ({codes_str})",
                            summary_hint=(
                                f"{source} found {count} type error(s) in the codebase. "
                                f"Top error codes: {codes_str}. "
                                "Resolve the type errors, preferring targeted annotations over broad `# type: ignore` suppressions. "
                                "Keep the change scoped to type fixes."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="type_fix",
                        ),
                        priority=(1, 5, f"type_fix|errors_present|{count}"),
                    )
                )

            elif insight.dedup_key.endswith("worsened"):
                delta = int(insight.evidence.get("delta", 0))
                current = int(insight.evidence.get("current_count", 0))
                distinct_files = insight.evidence.get("distinct_file_count")
                candidates.append(
                    CandidateSpec(
                        family="type_fix",
                        subject="type_errors",
                        pattern_key="errors_worsened",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "type_errors_count_increased",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="high",
                        evidence_lines=[
                            f"Type errors increased by {delta} (now {current} total).",
                        ],
                        risk_class="logic",
                        expires_after_runs=4,
                        estimated_affected_files=int(distinct_files) if distinct_files is not None else None,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Fix type regression: +{delta} new type error(s)",
                            summary_hint=(
                                f"Type error count increased by {delta} since the last observer snapshot "
                                f"(now {current} total). "
                                "Identify and resolve the newly introduced type errors before they accumulate further."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="type_fix",
                        ),
                        priority=(1, 4, f"type_fix|errors_worsened|{delta}"),
                    )
                )

        return candidates
