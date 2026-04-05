from __future__ import annotations

from dataclasses import dataclass, field

from control_plane.decision.models import CandidateRationale, EvidenceBundle, ProposalCandidate, ProposalOutline
from control_plane.decision.validation_profiles import profile_for_family


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
    estimated_affected_files: int | None = None
    validation_profile: str = ""


def _synthesize_evidence_bundle(
    family: str, evidence: dict[str, object]
) -> EvidenceBundle | None:
    """Build a structured EvidenceBundle from a raw evidence dict.

    Only families with a stable, well-known evidence schema are handled here.
    Other families return None and rely solely on evidence_lines.
    """
    if family == "lint_fix":
        count_raw = evidence.get("violation_count") or evidence.get("current_count")
        return EvidenceBundle(
            kind="lint_count",
            count=int(count_raw) if count_raw is not None else None,
            distinct_file_count=int(evidence["distinct_file_count"]) if "distinct_file_count" in evidence else None,
            delta=int(evidence["delta"]) if "delta" in evidence else None,
            trend="worsening" if "delta" in evidence else "present",
            top_codes=[str(c) for c in evidence.get("top_codes", [])],  # type: ignore[arg-type]
            source=str(evidence.get("source", "ruff")),
        )
    if family == "type_fix":
        count_raw = evidence.get("error_count") or evidence.get("current_count")
        return EvidenceBundle(
            kind="type_count",
            count=int(count_raw) if count_raw is not None else None,
            distinct_file_count=int(evidence["distinct_file_count"]) if "distinct_file_count" in evidence else None,
            delta=int(evidence["delta"]) if "delta" in evidence else None,
            trend="worsening" if "delta" in evidence else "present",
            top_codes=[str(c) for c in evidence.get("top_codes", [])],  # type: ignore[arg-type]
            source=str(evidence.get("source", "type_checker")),
        )
    return None


class CandidateBuilder:
    def build(self, spec: CandidateSpec) -> ProposalCandidate:
        dedup_key = "|".join(["candidate", spec.family, spec.subject, spec.pattern_key])
        candidate_id = dedup_key.replace("|", ":")
        validation_profile = spec.validation_profile or profile_for_family(spec.family)
        evidence_bundle = _synthesize_evidence_bundle(spec.family, spec.evidence)
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
            validation_profile=validation_profile,
            evidence_bundle=evidence_bundle,
            rationale=CandidateRationale(matched_rules=spec.matched_rules, suppressed_by=[]),
            proposal_outline=spec.proposal_outline,
        )
