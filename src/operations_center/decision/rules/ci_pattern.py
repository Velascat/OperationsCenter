from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class CIPatternRule:
    """Turn ci_pattern insights into investigation/fix candidates.

    Fires on:
    - ci_checks/failing: consistently failing checks.
    - ci_checks/flaky: intermittently failing checks.
    """

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "ci_pattern":
                continue

            if insight.status == "failing":
                failing = insight.evidence.get("failing_checks", [])
                failure_rate = insight.evidence.get("failure_rate", 0.0)
                checks_str = ", ".join(str(c) for c in failing[:3]) if failing else "unknown"
                candidates.append(
                    CandidateSpec(
                        family="ci_pattern",
                        subject="ci_checks",
                        pattern_key="checks_failing",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "ci_checks_consistently_failing",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="high",
                        evidence_lines=[
                            f"CI checks consistently failing ({failure_rate:.0%} failure rate): {checks_str}.",
                        ],
                        risk_class="logic",
                        expires_after_runs=4,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Investigate failing CI checks: {checks_str}",
                            summary_hint=(
                                f"The following CI checks are consistently failing across recent commits: {checks_str}. "
                                f"Overall CI failure rate: {failure_rate:.0%}. "
                                "Investigate root cause and fix the underlying issue. "
                                "Do not suppress or skip the checks without resolving the root cause."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="ci_pattern",
                        ),
                        priority=(1, 4, f"ci_pattern|failing|{len(failing)}"),
                    )
                )

            elif insight.status == "flaky":
                flaky = insight.evidence.get("flaky_checks", [])
                failure_rate = insight.evidence.get("failure_rate", 0.0)
                checks_str = ", ".join(str(c) for c in flaky[:3]) if flaky else "unknown"
                candidates.append(
                    CandidateSpec(
                        family="ci_pattern",
                        subject="ci_checks",
                        pattern_key="checks_flaky",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "ci_checks_intermittently_failing",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="medium",
                        evidence_lines=[
                            f"CI checks showing intermittent failures: {checks_str}.",
                        ],
                        risk_class="logic",
                        expires_after_runs=5,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Stabilize flaky CI checks: {checks_str}",
                            summary_hint=(
                                f"The following CI checks are showing intermittent failures: {checks_str}. "
                                "Identify whether the flakiness is in the test suite, the environment, "
                                "or the check configuration, and stabilize accordingly. "
                                "Keep the change scoped to the identified flaky checks."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="ci_pattern",
                        ),
                        priority=(1, 5, f"ci_pattern|flaky|{len(flaky)}"),
                    )
                )

        return candidates
