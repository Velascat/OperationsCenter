# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
tuning/routing_recommend.py — derive StrategyFindings and RoutingTuningProposals.

Two functions:
  derive_findings(summaries, records) -> list[StrategyFinding]
  generate_recommendations(findings, policy_guardrails=...) -> list[RoutingTuningProposal]

Findings are bounded observations. Recommendations are candidate changes
that always require human review (requires_review=True).

Design rules:
  - Weak-evidence summaries produce sparse_data findings only.
  - No recommendation is generated from WEAK evidence alone.
  - Contradictory signals (e.g. high success but poor change evidence) are
    surfaced as notes rather than suppressed.
  - Every recommendation carries its source_finding_ids.
"""

from __future__ import annotations

from operations_center.observability.models import ExecutionRecord

from .routing_models import (
    ChangeEvidenceClass,
    EvidenceStrength,
    LatencyClass,
    ReliabilityClass,
    RoutingTuningProposal,
    StrategyFinding,
    BackendComparisonSummary,
)

# Minimum evidence strength to generate a recommendation.
_MIN_STRENGTH_FOR_RECOMMENDATION = EvidenceStrength.MODERATE

# Timeout rate above which a finding is generated.
_HIGH_TIMEOUT_RATE = 0.20

# Validation skip rate above which a finding notes the gap.
_HIGH_VALIDATION_SKIP_RATE = 0.80

# "Acceptable outcome quality" threshold for contradictory success-vs-auditability signals.
_ACCEPTABLE_SUCCESS_WITH_POOR_CHANGE_EVIDENCE = 0.75


def derive_findings(
    summaries: list[BackendComparisonSummary],
    records: list[ExecutionRecord] | None = None,
) -> list[StrategyFinding]:
    """Derive StrategyFindings from comparison summaries.

    Findings categories produced:
      sparse_data      — sample too small for confident analysis
      reliability      — success/failure rate patterns
      change_evidence  — quality of changed-file evidence
      validation       — validation coverage gap
      latency          — latency class
      contradictory    — conflicting signals
    """
    findings: list[StrategyFinding] = []
    for summary in summaries:
        findings.extend(_findings_for_summary(summary))
    return findings


def generate_recommendations(
    findings: list[StrategyFinding],
    *,
    policy_guardrails: list[str] | None = None,
) -> list[RoutingTuningProposal]:
    """Generate candidate RoutingTuningProposals from findings.

    Rules:
      - Only MODERATE or STRONG evidence findings produce recommendations.
      - Each recommendation must have at least one source_finding_id.
      - All recommendations have requires_review=True.
    """
    proposals: list[RoutingTuningProposal] = []
    for finding in findings:
        if finding.evidence_strength == EvidenceStrength.WEAK:
            continue
        proposal = _proposal_for_finding(
            finding,
            policy_guardrails=policy_guardrails or [],
        )
        if proposal is not None:
            proposals.append(proposal)
    return proposals


# ---------------------------------------------------------------------------
# Finding derivation
# ---------------------------------------------------------------------------


def _findings_for_summary(s: BackendComparisonSummary) -> list[StrategyFinding]:
    findings: list[StrategyFinding] = []

    # Sparse data — always emit this before other findings for small samples
    if s.evidence_strength == EvidenceStrength.WEAK:
        findings.append(StrategyFinding(
            category="sparse_data",
            summary=(
                f"{s.backend} @ {s.lane} has only {s.sample_size} sample(s) — "
                "too few for confident routing strategy conclusions."
            ),
            evidence_strength=EvidenceStrength.WEAK,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data={"sample_size": s.sample_size},
        ))
        return findings  # don't pile on with weak-evidence findings

    data: dict[str, object] = {
        "sample_size": s.sample_size,
        "success_rate": s.success_rate,
        "failure_rate": s.failure_rate,
        "timeout_rate": s.timeout_rate,
        "validation_skip_rate": s.validation_skip_rate,
        "validation_pass_rate": s.validation_pass_rate,
        "change_evidence_class": s.change_evidence_class.value,
        "latency_class": s.latency_class.value,
    }

    # Reliability finding
    if s.reliability_class == ReliabilityClass.HIGH:
        findings.append(StrategyFinding(
            category="reliability",
            summary=(
                f"{s.backend} @ {s.lane} shows high reliability: "
                f"{s.success_rate:.0%} success rate across {s.sample_size} runs."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
        ))
    elif s.reliability_class == ReliabilityClass.LOW:
        findings.append(StrategyFinding(
            category="reliability",
            summary=(
                f"{s.backend} @ {s.lane} shows low reliability: "
                f"{s.success_rate:.0%} success rate across {s.sample_size} runs."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
        ))

    # Timeout finding
    if s.timeout_rate > _HIGH_TIMEOUT_RATE:
        findings.append(StrategyFinding(
            category="reliability",
            summary=(
                f"{s.backend} @ {s.lane} has a high timeout rate of "
                f"{s.timeout_rate:.0%} across {s.sample_size} runs."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
        ))

    # Change evidence finding
    if s.change_evidence_class == ChangeEvidenceClass.POOR:
        findings.append(StrategyFinding(
            category="change_evidence",
            summary=(
                f"{s.backend} @ {s.lane} produces poor changed-file evidence: "
                "most runs do not report which files changed."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
        ))
    elif s.change_evidence_class == ChangeEvidenceClass.PARTIAL:
        findings.append(StrategyFinding(
            category="change_evidence",
            summary=(
                f"{s.backend} @ {s.lane} produces partial changed-file evidence: "
                "some runs do not report which files changed."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
        ))

    # Validation coverage finding
    if s.validation_skip_rate > _HIGH_VALIDATION_SKIP_RATE:
        findings.append(StrategyFinding(
            category="validation",
            summary=(
                f"{s.backend} @ {s.lane} skips validation in "
                f"{s.validation_skip_rate:.0%} of runs, limiting quality signal."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
        ))

    # Latency finding
    if s.latency_class == LatencyClass.SLOW:
        findings.append(StrategyFinding(
            category="latency",
            summary=(
                f"{s.backend} @ {s.lane} is slow "
                f"(median {s.median_duration_ms} ms) across {s.sample_size} runs."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
        ))

    # Contradictory signal: high reliability but poor change evidence
    if (
        s.success_rate >= _ACCEPTABLE_SUCCESS_WITH_POOR_CHANGE_EVIDENCE
        and s.change_evidence_class == ChangeEvidenceClass.POOR
    ):
        findings.append(StrategyFinding(
            category="contradictory",
            summary=(
                f"{s.backend} @ {s.lane} is reliable by success rate but produces "
                "poor changed-file evidence — tasks succeed without reporting what changed."
            ),
            evidence_strength=s.evidence_strength,
            affected_lanes=[s.lane],
            affected_backends=[s.backend],
            task_scope=s.task_type_scope,
            supporting_data=data,
            notes=(
                "Consider restricting this backend to contexts where "
                "change enumeration is not a hard requirement."
            ),
        ))

    return findings


# ---------------------------------------------------------------------------
# Recommendation generation
# ---------------------------------------------------------------------------


def _proposal_for_finding(
    finding: StrategyFinding,
    *,
    policy_guardrails: list[str],
) -> RoutingTuningProposal | None:
    cat = finding.category
    lane = finding.affected_lanes[0] if finding.affected_lanes else "unknown"
    backend = finding.affected_backends[0] if finding.affected_backends else "unknown"

    if cat == "reliability" and "low reliability" in finding.summary:
        return RoutingTuningProposal(
            summary=f"Consider demoting {backend} @ {lane} for high-risk or validation-required tasks.",
            proposed_change=(
                f"Reduce preference for {backend} @ {lane} in routing policy "
                "for risk=high or task types requiring reliable outcomes."
            ),
            justification=finding.summary,
            evidence_strength=finding.evidence_strength,
            affected_policy_area="backend_preference",
            risk_notes=(
                "Verify this pattern holds for specific task types before adjusting routing. "
                "Low overall success rate may reflect a subset of particularly hard tasks."
            ),
            source_finding_ids=[finding.finding_id],
            policy_guardrails=policy_guardrails,
        )

    if cat == "reliability" and "high reliability" in finding.summary:
        return RoutingTuningProposal(
            summary=f"Consider increasing preference for {backend} @ {lane} for bounded tasks.",
            proposed_change=(
                f"Increase routing preference for {backend} @ {lane} for "
                "low/medium risk tasks where it has demonstrated strong reliability."
            ),
            justification=finding.summary,
            evidence_strength=finding.evidence_strength,
            affected_policy_area="backend_preference",
            risk_notes=(
                "Confirm that high success rate is not a result of easy/trivial tasks dominating "
                "the sample before broadening preference."
            ),
            source_finding_ids=[finding.finding_id],
            policy_guardrails=policy_guardrails,
        )

    if cat == "reliability" and "high timeout rate" in finding.summary:
        return RoutingTuningProposal(
            summary=f"Consider lowering timeout threshold or restricting {backend} @ {lane} scope.",
            proposed_change=(
                f"For {backend} @ {lane}: reduce timeout_seconds or "
                "add escalation path when task complexity is high."
            ),
            justification=finding.summary,
            evidence_strength=finding.evidence_strength,
            affected_policy_area="escalation_threshold",
            risk_notes="Lowering timeout may cause premature failures on legitimately long tasks.",
            source_finding_ids=[finding.finding_id],
            policy_guardrails=policy_guardrails,
        )

    if cat == "change_evidence" and "poor" in finding.summary:
        return RoutingTuningProposal(
            summary=(
                f"Restrict {backend} @ {lane} to contexts where "
                "change enumeration is not required."
            ),
            proposed_change=(
                f"Add routing policy note: {backend} @ {lane} should not be preferred "
                "for tasks where changed-file auditability is a requirement "
                "(e.g. compliance workflows, audited migrations)."
            ),
            justification=finding.summary,
            evidence_strength=finding.evidence_strength,
            affected_policy_area="backend_preference",
            risk_notes=(
                "Poor change evidence may reflect a backend design limitation, "
                "not a signal of incorrectness. Confirm before restricting."
            ),
            source_finding_ids=[finding.finding_id],
            policy_guardrails=policy_guardrails,
        )

    if cat == "validation":
        return RoutingTuningProposal(
            summary=(
                f"Consider requiring validation commands for {backend} @ {lane} runs "
                "on medium/high-risk tasks."
            ),
            proposed_change=(
                f"For {backend} @ {lane} on risk=medium or risk=high: "
                "policy should require at least one validation_command before execution."
            ),
            justification=finding.summary,
            evidence_strength=finding.evidence_strength,
            affected_policy_area="validation_requirements",
            risk_notes=(
                "Enforcing validation where currently skipped may surface pre-existing "
                "test failures that were previously ignored."
            ),
            source_finding_ids=[finding.finding_id],
            policy_guardrails=policy_guardrails,
        )

    if cat == "latency":
        return RoutingTuningProposal(
            summary=(
                f"Consider adjusting local-first threshold when {backend} @ {lane} "
                "is consistently slow for bounded tasks."
            ),
            proposed_change=(
                f"For low-risk bounded tasks currently routed to {backend} @ {lane}: "
                "evaluate whether a faster local lane would be acceptable."
            ),
            justification=finding.summary,
            evidence_strength=finding.evidence_strength,
            affected_policy_area="local_first_threshold",
            risk_notes=(
                "Local lanes may not have equivalent capability for all task types. "
                "Confirm capability before shifting routing preference."
            ),
            source_finding_ids=[finding.finding_id],
            policy_guardrails=policy_guardrails,
        )

    if cat == "contradictory":
        return RoutingTuningProposal(
            summary=(
                f"Flag {backend} @ {lane} for manual review: "
                "succeeds frequently but poor change audit trail."
            ),
            proposed_change=(
                f"Add documentation note: {backend} @ {lane} is acceptable for "
                "tasks where outcome matters more than file-level auditability. "
                "Consider restricting for compliance or audit-critical workflows."
            ),
            justification=finding.summary,
            evidence_strength=finding.evidence_strength,
            affected_policy_area="backend_preference",
            risk_notes=finding.notes,
            source_finding_ids=[finding.finding_id],
            policy_guardrails=policy_guardrails,
        )

    return None
