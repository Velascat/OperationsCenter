from __future__ import annotations

from control_plane.tuning.models import FamilyMetrics, TuningRecommendation

# Minimum sample runs before any recommendation is issued.
MIN_SAMPLE_FOR_RECOMMENDATION = 5

# A family suppressed at this rate or above is flagged as over-suppressed.
OVER_SUPPRESSED_RATE = 0.90

# A family with create_rate at or below this AND sufficient emissions is noisy/low-value.
NOISY_CREATE_RATE_CEILING = 0.10
NOISY_MIN_EMITTED = 5

# A family at or above this create_rate with sufficient emissions is healthy.
HEALTHY_CREATE_RATE_FLOOR = 0.25
HEALTHY_MIN_EMITTED = 3

# A family with no emissions and no suppressions after this many runs is silent.
# Use a separate, slightly lower floor because silence is cheaper to detect.
SILENT_SAMPLE_FLOOR = 5


class RecommendationEngine:
    """Converts per-family behavior metrics into conservative tuning recommendations.

    Rules are deterministic and tied to explicit thresholds. Every recommendation
    includes the evidence that drove it so the audit trail is self-explanatory.
    """

    def evaluate(self, metrics: list[FamilyMetrics]) -> list[TuningRecommendation]:
        return [self._evaluate_one(m) for m in metrics]

    def _evaluate_one(self, m: FamilyMetrics) -> TuningRecommendation:
        family = m.family
        evidence: dict[str, object] = {
            "sample_runs": m.sample_runs,
            "candidates_emitted": m.candidates_emitted,
            "candidates_suppressed": m.candidates_suppressed,
            "candidates_created": m.candidates_created,
            "candidates_skipped": m.candidates_skipped,
            "suppression_rate": m.suppression_rate,
            "create_rate": m.create_rate,
        }

        # Insufficient data — make no claim
        if m.sample_runs < MIN_SAMPLE_FOR_RECOMMENDATION:
            return TuningRecommendation(
                family=family,
                action="no_data",
                rationale=(
                    f"Only {m.sample_runs} decision run(s) in window; "
                    f"need at least {MIN_SAMPLE_FOR_RECOMMENDATION} before making recommendations."
                ),
                confidence="low",
                evidence=evidence,
            )

        # Silent — no signal at all, not even suppressions
        if m.candidates_emitted == 0 and m.candidates_suppressed == 0:
            return TuningRecommendation(
                family=family,
                action="review",
                rationale=(
                    f"No candidates emitted or suppressed for '{family}' across {m.sample_runs} runs. "
                    "Either no matching insights are being derived, or the family is gated. "
                    "Review whether the insight deriver and rule are producing output."
                ),
                confidence="medium",
                evidence=evidence,
            )

        # Over-suppressed — nearly all candidates are suppressed before reaching the board
        if m.suppression_rate >= OVER_SUPPRESSED_RATE:
            top_reason = (
                max(m.top_suppression_reasons, key=lambda k: m.top_suppression_reasons[k])
                if m.top_suppression_reasons
                else "unknown"
            )
            return TuningRecommendation(
                family=family,
                action="loosen_threshold",
                rationale=(
                    f"'{family}' has a {m.suppression_rate:.0%} suppression rate across {m.sample_runs} runs "
                    f"(top reason: {top_reason}). "
                    "The threshold may be too strict; consider lowering min_consecutive_runs by 1."
                ),
                confidence="high" if m.sample_runs >= 10 else "medium",
                evidence={**evidence, "top_suppression_reason": top_reason},
                suggested_change={"min_consecutive_runs": {"direction": "decrease", "step": 1}},
            )

        # Noisy/low-value — emitting candidates but rarely creating useful tasks
        if (
            m.candidates_emitted >= NOISY_MIN_EMITTED
            and m.create_rate <= NOISY_CREATE_RATE_CEILING
        ):
            return TuningRecommendation(
                family=family,
                action="tighten_threshold",
                rationale=(
                    f"'{family}' emitted {m.candidates_emitted} candidates but only created "
                    f"{m.candidates_created} ({m.create_rate:.0%} create rate). "
                    "Most emitted candidates are not reaching the board as useful tasks. "
                    "Consider raising min_consecutive_runs by 1."
                ),
                confidence="high" if m.candidates_emitted >= 10 else "medium",
                evidence=evidence,
                suggested_change={"min_consecutive_runs": {"direction": "increase", "step": 1}},
            )

        # Healthy — emitting and creating at a reasonable rate
        if m.candidates_emitted >= HEALTHY_MIN_EMITTED and m.create_rate >= HEALTHY_CREATE_RATE_FLOOR:
            return TuningRecommendation(
                family=family,
                action="keep",
                rationale=(
                    f"'{family}' shows a healthy {m.create_rate:.0%} create rate "
                    f"from {m.candidates_emitted} emitted candidates across {m.sample_runs} runs. "
                    "No threshold adjustment recommended."
                ),
                confidence="high" if m.candidates_emitted >= 5 else "medium",
                evidence=evidence,
            )

        # Moderate / not enough signal yet to have a strong view
        return TuningRecommendation(
            family=family,
            action="review",
            rationale=(
                f"'{family}' has {m.candidates_emitted} emitted and "
                f"{m.candidates_created} created across {m.sample_runs} runs "
                f"(create rate: {m.create_rate:.0%}). "
                "Not enough data for a confident threshold recommendation; monitor for more runs."
            ),
            confidence="low",
            evidence=evidence,
        )
