from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class TodoAccumulationRule:
    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "todo_concentration":
                continue
            current_total = int(insight.evidence.get("current_total", 0))
            previous_total = int(insight.evidence.get("previous_total", 0))
            if insight.dedup_key.endswith("count_changed") and current_total > previous_total:
                delta = current_total - previous_total
                candidates.append(
                    CandidateSpec(
                        family="todo_accumulation",
                        subject="todo_fixme_total",
                        pattern_key="count_increased",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "todo_fixme_total_increased",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="medium",
                        evidence_lines=[
                            f"TODO/FIXME total increased by {delta} (from {previous_total} to {current_total}).",
                        ],
                        risk_class="style",
                        expires_after_runs=4,
                        proposal_outline=ProposalOutline(
                            title_hint="Investigate recent TODO/FIXME accumulation",
                            summary_hint=(
                                "Recent observer history shows the total TODO/FIXME count increased. "
                                "Create one bounded follow-up to inspect the concentrated accumulation."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="todo_accumulation",
                        ),
                        priority=(4, 0, "todo_accumulation|count_increased"),
                    )
                )
            if insight.dedup_key.endswith("fixme|present"):
                fixme_count = int(insight.evidence.get("fixme_count", 0))
                candidates.append(
                    CandidateSpec(
                        family="todo_accumulation",
                        subject="fixme",
                        pattern_key="fixme_present",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "fixme_present",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="medium",
                        evidence_lines=[
                            f"{fixme_count} FIXME marker(s) present in the codebase.",
                        ],
                        risk_class="style",
                        expires_after_runs=4,
                        proposal_outline=ProposalOutline(
                            title_hint="Review persistent FIXME presence",
                            summary_hint=(
                                "Current observer signals show FIXME markers are present. "
                                "Create one bounded follow-up to inspect whether the concentration is still warranted."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="todo_accumulation",
                        ),
                        priority=(4, 1, "todo_accumulation|fixme_present"),
                    )
                )
        return candidates
