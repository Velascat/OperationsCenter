from __future__ import annotations

from collections.abc import Sequence

from control_plane.decision.candidate_builder import CandidateSpec
from control_plane.decision.models import ProposalOutline
from control_plane.insights.models import DerivedInsight


class ObservationCoverageRule:
    def __init__(self, *, min_consecutive_runs: int) -> None:
        self.min_consecutive_runs = min_consecutive_runs

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "observation_coverage":
                continue
            if (
                insight.dedup_key.endswith("persistent_unavailable")
                and int(insight.evidence.get("consecutive_snapshots", 0)) >= self.min_consecutive_runs
            ):
                signal = str(insight.evidence.get("signal", insight.subject))
                candidates.append(
                    CandidateSpec(
                        family="observation_coverage",
                        subject=signal,
                        pattern_key="repeated_unavailable",
                        evidence=dict(insight.evidence),
                        matched_rules=[
                            "observation_signal_repeated_unavailable",
                            "candidate_not_seen_in_cooldown_window",
                        ],
                        proposal_outline=ProposalOutline(
                            title_hint=f"Restore repeated missing {signal} coverage",
                            summary_hint=(
                                "Observer coverage shows this signal has remained unavailable across repeated runs. "
                                "Create one bounded follow-up to restore the missing visibility."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="observation_coverage",
                        ),
                        priority=(0, 0, f"observation_coverage|{signal}|repeated_unavailable"),
                    )
                )
        return candidates
