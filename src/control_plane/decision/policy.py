from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from control_plane.decision.candidate_builder import CandidateBuilder, CandidateSpec
from control_plane.decision.models import ProposalCandidate, ProposalCandidatesArtifact, SuppressedCandidate
from control_plane.decision.suppression import suppressed_candidate


@dataclass(frozen=True)
class DecisionPolicyConfig:
    max_candidates: int = 3
    max_candidates_per_family: int = 1
    cooldown_minutes: int = 120


class DecisionPolicy:
    def __init__(self, *, config: DecisionPolicyConfig, builder: CandidateBuilder | None = None) -> None:
        self.config = config
        self.builder = builder or CandidateBuilder()

    def apply(
        self,
        *,
        candidate_specs: list[CandidateSpec],
        prior_artifacts: list[ProposalCandidatesArtifact],
        generated_at: datetime,
    ) -> tuple[list[ProposalCandidate], list[SuppressedCandidate]]:
        emitted: list[ProposalCandidate] = []
        suppressed: list[SuppressedCandidate] = []
        emitted_dedup_keys: set[str] = set()
        emitted_per_family: dict[str, int] = {}
        cooldown_cutoff = generated_at - timedelta(minutes=self.config.cooldown_minutes)

        for spec in sorted(candidate_specs, key=lambda item: item.priority):
            candidate = self.builder.build(spec)
            if candidate.dedup_key in emitted_dedup_keys:
                suppressed.append(
                    suppressed_candidate(
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        subject=candidate.subject,
                        reason="duplicate_in_run",
                        evidence={},
                    )
                )
                continue
            last_emitted_at = self._last_emitted_at(candidate.dedup_key, prior_artifacts)
            if last_emitted_at is not None and last_emitted_at >= cooldown_cutoff:
                suppressed.append(
                    suppressed_candidate(
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        subject=candidate.subject,
                        reason="cooldown_active",
                        evidence={
                            "last_emitted_at": last_emitted_at.isoformat(),
                            "cooldown_minutes": self.config.cooldown_minutes,
                        },
                    )
                )
                continue
            if emitted_per_family.get(candidate.family, 0) >= self.config.max_candidates_per_family:
                suppressed.append(
                    suppressed_candidate(
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        subject=candidate.subject,
                        reason="family_quota_exceeded",
                        evidence={"max_candidates_per_family": self.config.max_candidates_per_family},
                    )
                )
                continue
            if len(emitted) >= self.config.max_candidates:
                suppressed.append(
                    suppressed_candidate(
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        subject=candidate.subject,
                        reason="quota_exceeded",
                        evidence={"max_candidates": self.config.max_candidates},
                    )
                )
                continue
            emitted.append(candidate)
            emitted_dedup_keys.add(candidate.dedup_key)
            emitted_per_family[candidate.family] = emitted_per_family.get(candidate.family, 0) + 1
        return emitted, suppressed

    def _last_emitted_at(
        self,
        dedup_key: str,
        prior_artifacts: list[ProposalCandidatesArtifact],
    ) -> datetime | None:
        for artifact in prior_artifacts:
            for candidate in artifact.candidates:
                if candidate.dedup_key == dedup_key:
                    return artifact.generated_at
        return None
