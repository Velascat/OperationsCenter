# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class ValidationPatternRule:
    """Turn validation_pattern insights into investigation candidates.

    Fires when tasks have been executed multiple times and failed post-execution
    validation repeatedly, suggesting a systematic difficulty.
    """

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "validation_pattern":
                continue

            if insight.status == "repeated_failures":
                count = int(insight.evidence.get("tasks_with_repeated_failures", 0))
                worst_id = str(insight.evidence.get("worst_task_id", "unknown"))
                worst_failures = int(insight.evidence.get("worst_task_failure_count", 0))
                worst_runs = int(insight.evidence.get("worst_task_total_runs", 0))
                roles = insight.evidence.get("top_worker_roles", [])
                roles_str = ", ".join(str(r) for r in roles[:3]) if roles else "unknown"

                candidates.append(
                    CandidateSpec(
                        family="validation_pattern_followup",
                        subject="execution_tasks",
                        pattern_key="repeated_failures",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "validation_repeated_task_failures",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="high" if count >= 3 else "medium",
                        evidence_lines=[
                            f"{count} task(s) have repeated validation failures across multiple runs.",
                            f"Worst offender: task {worst_id[:8]}... failed {worst_failures}/{worst_runs} runs.",
                            f"Affected worker roles: {roles_str}.",
                        ],
                        risk_class="logic",
                        expires_after_runs=5,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Investigate {count} task(s) with repeated validation failures",
                            summary_hint=(
                                f"{count} task(s) have been executed multiple times and failed "
                                "post-execution validation on repeated attempts. "
                                f"The worst case is task {worst_id[:8]}... which failed validation "
                                f"{worst_failures} out of {worst_runs} runs. "
                                "This is a systemic signal: either the validation suite has persistent "
                                "failures, or specific tasks are scoped beyond what one execution pass "
                                "can resolve. "
                                "Investigate the validation artifacts for the identified tasks, fix the "
                                "root cause (broken test, misconfigured validator, or task scope issue), "
                                "and verify validation passes cleanly."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="validation_pattern_followup",
                        ),
                        priority=(0, 1, f"validation_pattern|repeated_failures|{count}"),
                    )
                )

        return candidates
