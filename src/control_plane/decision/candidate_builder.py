from __future__ import annotations

from dataclasses import dataclass, field

from control_plane.decision.models import CandidateRationale, ProposalCandidate, ProposalOutline


@dataclass(frozen=True)
class CandidateSpec:
    family: str
    subject: str
    pattern_key: str
    evidence: dict[str, object]
    matched_rules: list[str]
    proposal_outline: ProposalOutline
    priority: tuple[int, int, str] = field(default_factory=lambda: (99, 99, ""))
    confidence: str = "medium"
    evidence_lines: list[str] = field(default_factory=list)
    risk_class: str = "logic"
    expires_after_runs: int = 5


class CandidateBuilder:
    def build(self, spec: CandidateSpec) -> ProposalCandidate:
        dedup_key = "|".join(["candidate", spec.family, spec.subject, spec.pattern_key])
        candidate_id = dedup_key.replace("|", ":")
        return ProposalCandidate(
            candidate_id=candidate_id,
            dedup_key=dedup_key,
            family=spec.family,
            subject=spec.subject,
            status="emit",
            evidence=spec.evidence,
            confidence=spec.confidence,
            evidence_lines=list(spec.evidence_lines),
            risk_class=spec.risk_class,
            expires_after_runs=spec.expires_after_runs,
            rationale=CandidateRationale(matched_rules=spec.matched_rules, suppressed_by=[]),
            proposal_outline=spec.proposal_outline,
        )
