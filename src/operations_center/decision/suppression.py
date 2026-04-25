from __future__ import annotations

from operations_center.decision.models import SuppressedCandidate


def suppressed_candidate(
    *,
    dedup_key: str,
    family: str,
    subject: str,
    reason: str,
    evidence: dict[str, object],
) -> SuppressedCandidate:
    return SuppressedCandidate(
        dedup_key=dedup_key,
        family=family,
        subject=subject,
        reason=reason,
        evidence=evidence,
    )
