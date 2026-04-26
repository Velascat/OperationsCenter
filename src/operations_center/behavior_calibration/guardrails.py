"""Guardrails enforcing the anti-collapse invariant.

The one-way promotion pipeline:

    artifact data → findings → recommendations → human-approved change

Calibration outputs are evidence for humans and task creation, not executable
policy. These functions validate that structural invariants hold at runtime.
"""

from __future__ import annotations

from .errors import BehaviorCalibrationError
from .models import CalibrationRecommendation


class GuardrailViolation(BehaviorCalibrationError):
    """Raised when a guardrail constraint is violated."""


# Fields that must never appear in a CalibrationRecommendation — their presence
# would indicate the recommendation has drifted toward executable policy.
_FORBIDDEN_MUTATION_FIELDS = frozenset({
    "auto_apply",
    "apply_immediately",
    "execute",
    "mutate",
    "config_patch",
    "runtime_patch",
    "manifest_patch",
    "code_change",
})


def validate_recommendation_structure(rec: CalibrationRecommendation) -> None:
    """Validate that a recommendation satisfies all advisory-only constraints.

    Raises GuardrailViolation if any constraint is violated.
    """
    enforce_requires_human_review(rec)
    assert_no_mutation_fields(rec)
    _assert_has_supporting_findings(rec)


def enforce_requires_human_review(rec: CalibrationRecommendation) -> None:
    """Raise GuardrailViolation if requires_human_review is not True."""
    if not rec.requires_human_review:
        raise GuardrailViolation(
            f"Recommendation {rec.recommendation_id!r} has requires_human_review=False. "
            "All recommendations must require human review before any action is taken."
        )


def assert_no_mutation_fields(rec: CalibrationRecommendation) -> None:
    """Raise GuardrailViolation if the recommendation carries forbidden mutation fields."""
    data = rec.model_dump()
    violations = _FORBIDDEN_MUTATION_FIELDS & set(data.keys())
    # Also check metadata for forbidden keys
    metadata_violations = _FORBIDDEN_MUTATION_FIELDS & set(data.get("metadata", {}).keys())
    all_violations = violations | metadata_violations
    if all_violations:
        raise GuardrailViolation(
            f"Recommendation {rec.recommendation_id!r} contains forbidden mutation fields: "
            f"{sorted(all_violations)}. Recommendations must not carry executable policy."
        )


def _assert_has_supporting_findings(rec: CalibrationRecommendation) -> None:
    """Raise GuardrailViolation if the recommendation has no supporting findings."""
    if not rec.supporting_finding_ids:
        raise GuardrailViolation(
            f"Recommendation {rec.recommendation_id!r} has no supporting_finding_ids. "
            "Every recommendation must be anchored to at least one finding."
        )


def validate_all_recommendations(recs: list[CalibrationRecommendation]) -> None:
    """Validate every recommendation in a list. Raises on first violation."""
    for rec in recs:
        validate_recommendation_structure(rec)
