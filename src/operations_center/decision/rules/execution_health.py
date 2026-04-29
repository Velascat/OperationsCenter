# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class ExecutionHealthRule:
    """Turns execution-health insights into proposal candidates.

    Fires on three patterns:
    - high_no_op_rate: suggests reviewing what tasks are being generated and
      whether task descriptions are clear enough for the execution engine.
    - persistent_validation_failures: suggests fixing a systemic quality issue
      (broken test suite, lint errors, or tasks scoped beyond one pass).
    - repeated_unknown_failures: suggests investigating recent executions that
      ended with unknown or error outcomes to identify the root cause.
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
                no_op_pct = int(float(no_op_rate) * 100)
                candidates.append(
                    CandidateSpec(
                        family="execution_health_followup",
                        subject=repo,
                        pattern_key="high_no_op_rate",
                        evidence=dict(insight.evidence),
                        matched_rules=["execution_health_high_no_op_rate"],
                        confidence="high" if no_op_pct >= 80 else "medium",
                        evidence_lines=[
                            f"{no_op_pct}% of last {total} runs for '{repo}' were no-ops (no material changes).",
                        ],
                        risk_class="logic",
                        expires_after_runs=5,
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
                        confidence="high",
                        evidence_lines=[
                            f"{fail_count} recent runs for '{repo}' completed but failed post-execution validation.",
                        ],
                        risk_class="logic",
                        expires_after_runs=3,
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

            elif pattern == "repeated_unknown_failures":
                unknown_count = insight.evidence.get("unknown_count", 0)
                error_count = insight.evidence.get("error_count", 0)
                unknown_error_total = insight.evidence.get(
                    "unknown_error_total", unknown_count + error_count
                )
                total = insight.evidence.get("total_runs", 0)
                candidates.append(
                    CandidateSpec(
                        family="execution_health_followup",
                        subject=repo,
                        pattern_key="repeated_unknown_failures",
                        evidence=dict(insight.evidence),
                        matched_rules=["execution_health_repeated_unknown_failures"],
                        confidence="high",
                        evidence_lines=[
                            f"{unknown_error_total} of the last {total} runs for '{repo}' "
                            f"ended with unknown or error outcomes "
                            f"({unknown_count} unknown, {error_count} errors).",
                        ],
                        risk_class="logic",
                        expires_after_runs=5,
                        proposal_outline=ProposalOutline(
                            title_hint=(
                                f"Investigate repeated unknown/error failures in {repo} "
                                f"({unknown_error_total} recent failures)"
                            ),
                            summary_hint=(
                                f"Execution artifacts show {unknown_error_total} of the last {total} "
                                f"runs for {repo} ended with unknown or error outcomes "
                                f"({unknown_count} unknown, {error_count} errors). "
                                "This suggests the execution engine is encountering repeated unexplained "
                                "failures. Investigate recent kodo_plane artifacts and execution logs "
                                "to identify whether the cause is environmental (e.g. tooling misconfiguration, "
                                "missing dependencies) or task-related (e.g. impossible scope, malformed input). "
                                "While this fix-task remains unresolved, the circuit-breaker will skip "
                                "further task execution against this repo to avoid wasting budget."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="execution_health_followup",
                        ),
                        priority=(0, 0, f"execution_health|{repo}|repeated_unknown_failures"),
                    )
                )

        return candidates
