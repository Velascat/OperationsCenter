# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class HotspotConcentrationRule:
    def __init__(self, *, min_repeated_runs: int) -> None:
        self.min_repeated_runs = min_repeated_runs

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        repeated_by_subject: dict[str, int] = {}
        dominant_by_subject: dict[str, dict[str, object]] = {}
        for insight in insights:
            if insight.kind != "file_hotspot":
                continue
            if insight.dedup_key.endswith("repeated_presence"):
                repeated_by_subject[insight.subject] = int(insight.evidence.get("appears_in_recent_snapshots", 0))
            if insight.dedup_key.endswith("dominant_current"):
                dominant_by_subject[insight.subject] = dict(insight.evidence)

        candidates: list[CandidateSpec] = []
        for subject, appearances in sorted(repeated_by_subject.items()):
            if appearances < self.min_repeated_runs:
                continue
            evidence: dict[str, object] = {"appears_in_recent_snapshots": appearances}
            evidence.update(dominant_by_subject.get(subject, {}))
            candidates.append(
                CandidateSpec(
                    family="hotspot_concentration",
                    subject=subject,
                    pattern_key="persistent",
                    evidence=evidence,
                    matched_rules=[
                        "hotspot_repeated_presence_min_runs",
                        "candidate_not_seen_in_cooldown_window",
                    ],
                    confidence="medium",
                    evidence_lines=[
                        f"'{subject}' appeared in top hotspots across {appearances} recent snapshots.",
                    ],
                    risk_class="structural",
                    expires_after_runs=5,
                    proposal_outline=ProposalOutline(
                        title_hint=f"Investigate repeated hotspot concentration in {subject}",
                        summary_hint=(
                            "Recent snapshots repeatedly place this file at the top of hotspot summaries. "
                            "Create one bounded follow-up to inspect the concentration pattern."
                        ),
                        labels_hint=["task-kind: improve", "source: proposer"],
                        source_family="hotspot_concentration",
                    ),
                    priority=(3, 0, f"hotspot_concentration|{subject}|persistent"),
                )
            )
        return candidates
