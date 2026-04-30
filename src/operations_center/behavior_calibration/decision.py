# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""CalibrationDecision — the promotion artifact between recommendations and changes.

A CalibrationDecision records that a human has reviewed one or more
CalibrationRecommendations and approved a specific course of action.

This is the ONLY path through which a calibration output may influence
configuration, code, or pipeline behavior. It is not used by the runtime;
it exists to make the review barrier structural and auditable.

Lifecycle:
    recommendation (advisory) → CalibrationDecision (human-approved) → applied change

Nothing downstream of CalibrationDecision is implemented here. This module
defines the gate, not what lies beyond it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class CalibrationDecision(BaseModel, frozen=True):
    """Records a human-approved decision to act on one or more recommendations.

    A CalibrationDecision is created outside OperationsCenter (by a human or
    an authorized task runner) after reviewing CalibrationRecommendations.
    It must not be created automatically from calibration output.
    """

    decision_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Stable identifier for this decision.",
    )
    source_recommendation_ids: list[str] = Field(
        description="IDs of CalibrationRecommendations this decision acts on.",
    )
    approved_by: str = Field(
        description="Identifier of the human or authorized agent that approved this decision.",
    )
    approved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the decision was approved.",
    )
    decision_notes: str = Field(
        default="",
        description="Free-text rationale or notes from the approver.",
    )
    applied_changes_reference: str = Field(
        default="",
        description=(
            "Reference to the artifact representing the applied change, e.g. a PR URL, "
            "issue number, or task ID. Empty if change is pending."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_applied(self) -> bool:
        return bool(self.applied_changes_reference)


__all__ = ["CalibrationDecision"]
