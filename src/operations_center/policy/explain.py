# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
policy/explain.py — generate human-readable explanations for PolicyDecisions.

explain() takes a PolicyDecision and optionally a RepoPolicy and returns a
PolicyExplanation with structured reasoning text.

This is useful for:
- logging why a run was blocked or required review
- surfacing to an operator why a task was gated
- retaining alongside execution records for later inspection
"""

from __future__ import annotations

from typing import Optional

from .models import PolicyDecision, PolicyExplanation, PolicyStatus, RepoPolicy


def explain(
    decision: PolicyDecision,
    policy: Optional[RepoPolicy] = None,
) -> PolicyExplanation:
    """Generate a human-readable explanation for a PolicyDecision.

    Args:
        decision: The evaluated PolicyDecision.
        policy:   Optional RepoPolicy used for additional context.

    Returns:
        A frozen PolicyExplanation with structured reasoning text.
    """
    summary = _build_summary(decision)
    key_rules = _key_rules_applied(decision)
    review_reasoning = _review_reasoning(decision)
    validation_reasoning = _validation_reasoning(decision)
    scope_reasoning = _scope_reasoning(decision)
    routing_reasoning = _routing_reasoning(decision)

    return PolicyExplanation(
        summary=summary,
        key_rules_applied=key_rules,
        review_reasoning=review_reasoning,
        validation_reasoning=validation_reasoning,
        scope_reasoning=scope_reasoning,
        routing_reasoning=routing_reasoning,
    )


# ---------------------------------------------------------------------------
# Reasoning builders
# ---------------------------------------------------------------------------


def _build_summary(decision: PolicyDecision) -> str:
    if decision.status == PolicyStatus.BLOCK:
        blocking = [v for v in decision.violations if v.blocking]
        if blocking:
            first = blocking[0]
            return f"BLOCKED: {first.message}"
        return "BLOCKED by policy"

    if decision.status == PolicyStatus.REQUIRE_REVIEW:
        review_viols = [v for v in decision.violations if not v.blocking and v.category == "review"]
        if review_viols:
            return f"REVIEW REQUIRED: {review_viols[0].message}"
        non_blocking = [v for v in decision.violations if not v.blocking]
        if non_blocking:
            return f"REVIEW REQUIRED: {non_blocking[0].message}"
        return "REVIEW REQUIRED by policy"

    if decision.status == PolicyStatus.ALLOW_WITH_WARNINGS:
        if decision.warnings:
            return f"ALLOWED WITH WARNINGS: {decision.warnings[0].message}"
        return "ALLOWED WITH WARNINGS"

    return "ALLOWED: no policy restrictions apply"


def _key_rules_applied(decision: PolicyDecision) -> list[str]:
    rules: list[str] = []
    for v in decision.violations:
        rules.append(v.rule_id)
    for w in decision.warnings:
        if w.rule_id not in rules:
            rules.append(w.rule_id)
    return rules


def _review_reasoning(decision: PolicyDecision) -> str:
    review_parts: list[str] = []
    for v in decision.violations:
        if v.category == "review":
            review_parts.append(v.message)
    if not review_parts:
        if decision.effective_review_requirement == "autonomous":
            return "Autonomous execution permitted by policy"
        return "Review required"
    return "; ".join(review_parts)


def _validation_reasoning(decision: PolicyDecision) -> str:
    val_parts: list[str] = []
    for v in decision.violations:
        if v.category == "validation":
            val_parts.append(v.message)
    for w in decision.warnings:
        if w.category == "validation":
            val_parts.append(w.message)
    if not val_parts:
        return f"Validation profile: {decision.effective_validation_profile!r}"
    return "; ".join(val_parts)


def _scope_reasoning(decision: PolicyDecision) -> str:
    path_parts: list[str] = []
    for v in decision.violations:
        if v.category == "path":
            path_parts.append(v.message)
    if decision.effective_scope:
        scope_desc = f"Effective scope: {decision.effective_scope}"
    else:
        scope_desc = "No path restriction declared (applies to all paths)"
    if path_parts:
        return scope_desc + "; " + "; ".join(path_parts)
    return scope_desc


def _routing_reasoning(decision: PolicyDecision) -> str:
    routing_parts: list[str] = []
    for v in decision.violations:
        if v.category == "routing":
            routing_parts.append(v.message)
    for v in decision.violations:
        if v.category == "tool" and "network" in v.rule_id:
            routing_parts.append(v.message)
    if not routing_parts:
        return "Routing selection is compatible with policy"
    return "; ".join(routing_parts)
