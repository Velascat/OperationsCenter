# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class DependencyDriftRule:
    def __init__(self, *, min_consecutive_runs: int) -> None:
        self.min_consecutive_runs = min_consecutive_runs

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if (
                insight.kind == "dependency_drift_continuity"
                and insight.subject == "dependency_drift"
                and insight.dedup_key.endswith("present|persistent")
                and int(insight.evidence.get("consecutive_snapshots", 0)) >= self.min_consecutive_runs
            ):
                consecutive = int(insight.evidence.get("consecutive_snapshots", 0))
                candidates.append(
                    CandidateSpec(
                        family="dependency_drift_followup",
                        subject="dependency_drift",
                        pattern_key="present_persistent",
                        evidence={"consecutive_snapshots": consecutive},
                        matched_rules=[
                            "dependency_drift_persistent_min_consecutive_runs",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        confidence="high" if consecutive >= 4 else "medium",
                        evidence_lines=[
                            f"Dependency drift present for {consecutive} consecutive snapshots.",
                        ],
                        risk_class="logic",
                        expires_after_runs=5,
                        proposal_outline=ProposalOutline(
                            title_hint="Investigate persistent dependency drift signal",
                            summary_hint=(
                                "Dependency drift has remained present across repeated runs. "
                                "Create one bounded follow-up to inspect the recurring drift signal."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="dependency_drift_followup",
                        ),
                        priority=(2, 0, "dependency_drift_followup|present_persistent"),
                    )
                )
        return candidates
