# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from operations_center.decision.models import CandidateRationale, EvidenceBundle, ProposalCandidate, ProposalOutline
from operations_center.decision.validation_profiles import profile_for_family


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
    ev = cast(dict[str, Any], evidence)
    if family == "lint_fix":
        count_raw = ev.get("violation_count")
        if count_raw is None:
            count_raw = ev.get("current_count")
        return EvidenceBundle(
            kind="lint_count",
            count=int(count_raw) if count_raw is not None else None,
            distinct_file_count=int(ev["distinct_file_count"]) if "distinct_file_count" in ev else None,
            delta=int(ev["delta"]) if "delta" in ev else None,
            trend="worsening" if ev.get("delta", 0) > 0 else ("improving" if ev.get("delta", 0) < 0 else "present"),
            top_codes=[str(c) for c in ev.get("top_codes", [])],
            source=str(ev.get("source", "ruff")),
        )
    if family == "type_fix":
        count_raw = ev.get("error_count")
        if count_raw is None:
            count_raw = ev.get("current_count")
        return EvidenceBundle(
            kind="type_count",
            count=int(count_raw) if count_raw is not None else None,
            distinct_file_count=int(ev["distinct_file_count"]) if "distinct_file_count" in ev else None,
            delta=int(ev["delta"]) if "delta" in ev else None,
            trend="worsening" if ev.get("delta", 0) > 0 else ("improving" if ev.get("delta", 0) < 0 else "present"),
            top_codes=[str(c) for c in ev.get("top_codes", [])],
            source=str(ev.get("source", "type_checker")),
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
