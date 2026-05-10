# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
observability/validation.py — Normalized validation evidence.

Wraps ValidationSummary from the canonical contracts into an observability-
oriented model that can carry artifact references and a human-readable summary.

If validation was skipped (the common case for the kodo adapter), the evidence
faithfully represents SKIPPED rather than inventing a pass/fail verdict.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from operations_center.contracts.common import ValidationSummary
from operations_center.contracts.enums import ValidationStatus


class ValidationEvidence(BaseModel):
    """Normalized validation outcome for an execution record.

    artifact_refs lists BackendDetailRef.ref_id values that point to raw
    validation output (logs, reports). May be empty if no artifacts exist.
    """

    status: ValidationStatus
    checks_run: int = Field(default=0, ge=0)
    checks_passed: int = Field(default=0, ge=0)
    checks_failed: int = Field(default=0, ge=0)
    summary: Optional[str] = Field(
        default=None,
        description="Human-readable summary; typically the failure excerpt when present.",
    )
    artifact_refs: list[str] = Field(
        default_factory=list,
        description="BackendDetailRef.ref_id values pointing to raw validation artifacts.",
    )

    model_config = {"frozen": True}


def normalize_validation(summary: ValidationSummary) -> ValidationEvidence:
    """Derive ValidationEvidence from a canonical ValidationSummary."""
    return ValidationEvidence(
        status=summary.status,
        checks_run=summary.commands_run,
        checks_passed=summary.commands_passed,
        checks_failed=summary.commands_failed,
        summary=summary.failure_excerpt,
    )
