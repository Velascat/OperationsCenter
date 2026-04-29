# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class TestVisibilityRule:
    def __init__(self, *, min_consecutive_runs: int) -> None:
        self.min_consecutive_runs = min_consecutive_runs

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind == "test_status_continuity" and insight.subject == "test_signal":
                current_status = str(insight.evidence.get("current_status", ""))
                consecutive = int(insight.evidence.get("consecutive_snapshots", 0))
                # Only 'unknown' triggers the persistent-unknown candidate.
                # 'discoverable' and 'no_config' indicate bounded fallback
                # succeeded, so they must NOT produce this candidate.
                if (
                    current_status == "unknown"
                    and insight.dedup_key.endswith("persistent")
                    and consecutive >= self.min_consecutive_runs
                ):
                    candidates.append(
                        CandidateSpec(
                            family="test_visibility",
                            subject="test_signal",
                            pattern_key="unknown_persistent",
                            evidence={
                                "current_status": current_status,
                                "consecutive_snapshots": consecutive,
                            },
                            matched_rules=[
                                "test_unknown_persistence_min_consecutive_runs",
                                "candidate_not_seen_in_cooldown_window",
                            ],
                            confidence="high" if consecutive >= 5 else "medium",
                            evidence_lines=[
                                f"Test signal status 'unknown' for {consecutive} consecutive snapshots.",
                            ],
                            risk_class="logic",
                            expires_after_runs=5,
                            proposal_outline=ProposalOutline(
                                title_hint="Improve test signal visibility for operations-center",
                                summary_hint=(
                                    "Test signal has remained unknown across recent observer snapshots. "
                                    "Add or repair bounded visibility so later autonomy passes can reason on explicit test state."
                                ),
                                labels_hint=["task-kind: improve", "source: proposer"],
                                source_family="test_visibility",
                            ),
                            priority=(1, 1, "test_visibility|unknown_persistent"),
                        )
                    )
                if (
                    insight.dedup_key.endswith("transition")
                    and current_status == "failed"
                    and insight.evidence.get("previous_status") == "passed"
                ):
                    candidates.append(
                        CandidateSpec(
                            family="test_visibility",
                            subject="test_signal",
                            pattern_key="passing_to_failing",
                            evidence={
                                "previous_status": "passed",
                                "current_status": "failed",
                            },
                            matched_rules=[
                                "test_status_transition_failed",
                                "candidate_not_seen_in_cooldown_window",
                            ],
                            confidence="high",
                            evidence_lines=[
                                "Test status transitioned from 'passed' to 'failed'.",
                            ],
                            risk_class="logic",
                            expires_after_runs=3,
                            proposal_outline=ProposalOutline(
                                title_hint="Investigate recent test status regression",
                                summary_hint=(
                                    "Recent insight history shows test status transitioned from passing to failing. "
                                    "Create one bounded follow-up to restore reliable test visibility or stability."
                                ),
                                labels_hint=["task-kind: improve", "source: proposer"],
                                source_family="test_visibility",
                            ),
                            priority=(1, 2, "test_visibility|passing_to_failing"),
                        )
                    )
            if (
                insight.kind == "observation_coverage"
                and insight.subject == "test_signal"
                and insight.dedup_key.endswith("persistent_unavailable")
            ):
                cov_consecutive = int(insight.evidence.get("consecutive_snapshots", 0))
                candidates.append(
                    CandidateSpec(
                        family="test_visibility",
                        subject="test_signal",
                        pattern_key="coverage_unavailable_persistent",
                        evidence={
                            "signal": "test_signal",
                            "consecutive_snapshots": cov_consecutive,
                        },
                        matched_rules=[
                            "test_signal_unavailable_repeated",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="high" if cov_consecutive >= 3 else "medium",
                        evidence_lines=[
                            f"Test signal unavailable in observer for {cov_consecutive} consecutive snapshots.",
                        ],
                        risk_class="logic",
                        expires_after_runs=5,
                        proposal_outline=ProposalOutline(
                            title_hint="Restore repeated missing test signal coverage",
                            summary_hint=(
                                "Observer coverage shows the test signal has been unavailable across repeated runs. "
                                "Add or repair one bounded visibility path for later autonomy stages."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="test_visibility",
                        ),
                        priority=(1, 0, "test_visibility|coverage_unavailable_persistent"),
                    )
                )
        return candidates
