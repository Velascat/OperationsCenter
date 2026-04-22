"""
policy/ — execution policy and guardrail layer (Phase 12).

This package defines the system's explicit policy boundaries. It constrains
planning, routing, and execution without being owned by any one backend or shell.

Public API:
    PolicyEngine          — evaluates proposals/decisions against guardrails
    PolicyDecision        — inspectable evaluation result
    PolicyStatus          — ALLOW / ALLOW_WITH_WARNINGS / REQUIRE_REVIEW / BLOCK
    PolicyConfig          — collection of RepoPolicy entries
    PolicyViolation       — specific rule violation (blocking or non-blocking)
    PolicyWarning         — non-blocking policy concern
    PolicyExplanation     — human-readable explanation of a PolicyDecision
    explain()             — generate PolicyExplanation from PolicyDecision
    validate_config()     — validate PolicyConfig for logical consistency
    DEFAULT_POLICY_CONFIG — conservative default policy configuration
"""

from .defaults import DEFAULT_POLICY_CONFIG, DEFAULT_REPO_POLICY
from .engine import PolicyEngine
from .explain import explain
from .models import (
    BranchGuardrail,
    PathPolicy,
    PathScopeRule,
    PolicyConfig,
    PolicyDecision,
    PolicyExplanation,
    PolicyStatus,
    PolicyViolation,
    PolicyWarning,
    RepoPolicy,
    ReviewRequirement,
    ToolGuardrail,
    ValidationRequirement,
)
from .validate import validate_config

__all__ = [
    "PolicyEngine",
    "PolicyDecision",
    "PolicyStatus",
    "PolicyConfig",
    "RepoPolicy",
    "PolicyViolation",
    "PolicyWarning",
    "PolicyExplanation",
    "BranchGuardrail",
    "PathPolicy",
    "PathScopeRule",
    "ToolGuardrail",
    "ValidationRequirement",
    "ReviewRequirement",
    "explain",
    "validate_config",
    "DEFAULT_POLICY_CONFIG",
    "DEFAULT_REPO_POLICY",
]
