from __future__ import annotations

from collections.abc import Sequence

from control_plane.decision.candidate_builder import CandidateSpec
from control_plane.decision.models import ProposalOutline
from control_plane.insights.models import DerivedInsight


class ExecutionHealthRule:
    """Turns execution-health insights into proposal candidates.

    Fires on two patterns:
    - high_no_op_rate: suggests reviewing what tasks are being generated and
      whether task descriptions are clear enough for the execution engine.
    - persistent_validation_failures: suggests fixing a systemic quality issue
      (broken test suite, lint errors, or tasks scoped beyond one pass).
    """

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "execution_health":
                continue

            repo = str(insight.evidence.get("repo", insight.subject))
            pattern = str(insight.evidence.get("pattern", ""))

            if pattern == "high_no_op_rate":
                no_op_rate = insight.evidence.get("no_op_rate", 0)
                total = insight.evidence.get("total_runs", 0)
                candidates.append(
                    CandidateSpec(
                        family="execution_health_followup",
                        subject=repo,
                        pattern_key="high_no_op_rate",
                        evidence=dict(insight.evidence),
                        matched_rules=["execution_health_high_no_op_rate"],
                        proposal_outline=ProposalOutline(
                            title_hint=(
                                f"Review task quality for {repo}: "
                                f"{int(float(no_op_rate) * 100)}% of recent runs produced no changes"
                            ),
                            summary_hint=(
                                f"Execution artifacts show {int(float(no_op_rate) * 100)}% of the last "
                                f"{total} runs for {repo} were no-ops (kodo ran but made no material changes). "
                                "This suggests tasks being generated may be too vague, already completed, "
                                "or scoped in a way the execution engine cannot act on. "
                                "Investigate one representative no-op run and tighten task descriptions or "
                                "proposer heuristics to improve actionability."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="execution_health_followup",
                        ),
                        priority=(1, 0, f"execution_health|{repo}|high_no_op_rate"),
                    )
                )

            elif pattern == "persistent_validation_failures":
                fail_count = insight.evidence.get("validation_failed_count", 0)
                candidates.append(
                    CandidateSpec(
                        family="execution_health_followup",
                        subject=repo,
                        pattern_key="persistent_validation_failures",
                        evidence=dict(insight.evidence),
                        matched_rules=["execution_health_persistent_validation_failures"],
                        proposal_outline=ProposalOutline(
                            title_hint=(
                                f"Fix recurring validation failures in {repo} "
                                f"({fail_count} recent failures)"
                            ),
                            summary_hint=(
                                f"Execution artifacts show {fail_count} runs for {repo} completed "
                                "but failed the post-execution validation step. "
                                "This is a systemic signal: either the test suite or linter has persistent "
                                "failures unrelated to the tasks being run, or recent tasks are consistently "
                                "scoped beyond what one execution pass can resolve cleanly. "
                                "Investigate recent validation.json artifacts to identify the failing command "
                                "and address the root cause. "
                                "While this fix-task remains unresolved, the circuit-breaker will skip "
                                "further task execution against this repo to avoid wasting budget."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="execution_health_followup",
                        ),
                        priority=(0, 0, f"execution_health|{repo}|persistent_validation_failures"),
                    )
                )

        return candidates
