# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import datetime, timedelta

from operations_center.decision.candidate_builder import CandidateBuilder, CandidateSpec
from operations_center.decision.models import ProposalCandidatesArtifact, SuppressedCandidate
from operations_center.decision.suppression import suppressed_candidate

# Upstream prerequisites per downstream family.
# When any listed upstream family is active (in-cycle or recently emitted),
# the downstream family is suppressed.
#
# Rationale for each chain:
# - type_fix after lint_fix: fixing lint first avoids re-touching the same lines
#   when applying type annotations, and keeps each PR reviewable independently.
# - execution_health_followup after test_visibility: if the test signal itself is
#   broken/unknown, execution health metrics are unreliable. Fix visibility first.
_FAMILY_PREREQUISITES: dict[str, list[str]] = {
    "type_fix": ["lint_fix"],
    "execution_health_followup": ["test_visibility"],
}


class ChainPolicy:
    """Suppresses downstream candidates when upstream prerequisites are active.

    Two suppression reasons are recorded:
    - ``upstream_family_in_cycle``: upstream candidate emitted in the same decision
      run as the downstream candidate. Prefer the upstream work first.
    - ``upstream_family_recently_emitted``: upstream was emitted in a recent prior run
      (within the cooldown window) and may still be in-progress.

    The policy is intentionally small: it only enforces the explicit chains in
    ``_FAMILY_PREREQUISITES``. Families not listed there are unaffected.
    """

    def __init__(self, *, cooldown_minutes: int = 120) -> None:
        self.cooldown_minutes = cooldown_minutes
        self._builder = CandidateBuilder()

    def apply(
        self,
        *,
        specs: list[CandidateSpec],
        prior_artifacts: list[ProposalCandidatesArtifact],
        generated_at: datetime,
    ) -> tuple[list[CandidateSpec], list[SuppressedCandidate]]:
        """Return (surviving specs, chain-suppressed records)."""
        in_cycle_families: set[str] = {spec.family for spec in specs}

        cooldown_cutoff = generated_at - timedelta(minutes=self.cooldown_minutes)
        recently_emitted_families: set[str] = set()
        for artifact in prior_artifacts:
            if artifact.generated_at >= cooldown_cutoff:
                for candidate in artifact.candidates:
                    if candidate.status == "emit":
                        recently_emitted_families.add(candidate.family)

        live: list[CandidateSpec] = []
        chain_suppressed: list[SuppressedCandidate] = []

        for spec in specs:
            prerequisites = _FAMILY_PREREQUISITES.get(spec.family)
            if not prerequisites:
                live.append(spec)
                continue

            blocking_upstream: str | None = None
            reason: str | None = None

            for upstream in prerequisites:
                if upstream in in_cycle_families:
                    blocking_upstream = upstream
                    reason = "upstream_family_in_cycle"
                    break
                if upstream in recently_emitted_families:
                    blocking_upstream = upstream
                    reason = "upstream_family_recently_emitted"
                    break

            if blocking_upstream is None:
                live.append(spec)
            else:
                candidate = self._builder.build(spec)
                chain_suppressed.append(
                    suppressed_candidate(
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        subject=candidate.subject,
                        reason=reason or "upstream_family_in_cycle",
                        evidence={
                            "upstream_family": blocking_upstream,
                            "downstream_family": spec.family,
                        },
                    )
                )

        return live, chain_suppressed
