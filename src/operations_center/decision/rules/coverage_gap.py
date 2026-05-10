# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CoverageGapRule — propose test coverage improvement tasks."""
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class CoverageGapRule:
    """Turns coverage_gap insights into test coverage improvement candidates.

    Fires on:
    - coverage_gap/low_overall  — total coverage is below threshold
    - coverage_gap/uncovered_files — specific files have low coverage
    """

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []

        for insight in insights:
            if insight.kind == "coverage_gap/low_overall":
                pct = insight.evidence.get("total_coverage_pct", 0)
                candidates.append(
                    CandidateSpec(
                        family="coverage_gap",
                        subject="coverage",
                        pattern_key="low_overall",
                        evidence=dict(insight.evidence),
                        matched_rules=["coverage_gap_low_overall"],
                        confidence="medium",
                        evidence_lines=[
                            f"Total test coverage is {pct}%, below the {insight.evidence.get('threshold_pct', 60)}% threshold.",
                            "Add tests for the most critical uncovered paths.",
                        ],
                        risk_class="quality",
                        expires_after_runs=5,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Improve test coverage (currently {pct}%)",
                            summary_hint=(
                                f"Test coverage is at {pct}%, below the recommended threshold. "
                                "Identify the most critical uncovered code paths and add targeted tests. "
                                "Focus on business logic, error handling, and edge cases rather than trivial getters."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="coverage_gap",
                        ),
                        priority=(2, 3, f"coverage_gap|low_overall|{pct}"),
                    )
                )

            elif insight.kind == "coverage_gap/uncovered_files":
                count = insight.evidence.get("uncovered_file_count", 0)
                top_files = insight.evidence.get("top_uncovered", [])
                files_str = ", ".join(str(f) for f in top_files[:3]) if top_files else "several files"
                threshold = insight.evidence.get("threshold_pct", 80)
                candidates.append(
                    CandidateSpec(
                        family="coverage_gap",
                        subject="coverage",
                        pattern_key="uncovered_files",
                        evidence=dict(insight.evidence),
                        matched_rules=["coverage_gap_uncovered_files"],
                        confidence="medium",
                        evidence_lines=[
                            f"{count} file(s) have coverage below {threshold}%.",
                            f"Lowest-coverage files: {files_str}.",
                        ],
                        risk_class="quality",
                        expires_after_runs=5,
                        proposal_outline=ProposalOutline(
                            title_hint=f"Add tests for {count} under-covered file(s)",
                            summary_hint=(
                                f"{count} file(s) have test coverage below {threshold}%. "
                                f"Priority files: {files_str}. "
                                "Add unit or integration tests for the uncovered branches in these files."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="coverage_gap",
                        ),
                        priority=(2, 2, f"coverage_gap|uncovered_files|{count}"),
                    )
                )

        return candidates
