# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class FamilyMetrics(BaseModel):
    family: str
    sample_runs: int
    candidates_emitted: int = 0
    candidates_suppressed: int = 0
    candidates_created: int = 0
    candidates_skipped: int = 0
    candidates_failed: int = 0
    suppression_rate: float = 0.0
    create_rate: float = 0.0
    no_creation_rate: float = 0.0
    top_suppression_reasons: dict[str, int] = Field(default_factory=dict)
    proposals_merged: int = 0
    proposals_escalated: int = 0
    acceptance_rate: float = 0.0  # merged / (merged + escalated), or 0 if no feedback


class TuningRecommendation(BaseModel):
    family: str
    action: str  # keep | loosen_threshold | tighten_threshold | review | no_data
    rationale: str
    confidence: str  # low | medium | high
    evidence: dict[str, object] = Field(default_factory=dict)
    # Specific parameter change suggested, e.g. {"min_consecutive_runs": {"from": 2, "to": 1}}
    suggested_change: dict[str, object] | None = None


class TuningChange(BaseModel):
    family: str
    key: str
    before: int
    after: int
    reason: str
    applied_at: datetime


class SkippedTuningChange(BaseModel):
    family: str
    intended_action: str
    reason: str  # cooldown_active | quota_exceeded | sample_too_small | outside_range | family_not_allowed
    evidence: dict[str, object] = Field(default_factory=dict)


class TuningRunArtifact(BaseModel):
    run_id: str
    generated_at: datetime
    source_command: str
    dry_run: bool = True
    auto_apply: bool = False
    window_runs: int
    window_start: datetime | None = None
    window_end: datetime | None = None
    family_metrics: list[FamilyMetrics] = Field(default_factory=list)
    recommendations: list[TuningRecommendation] = Field(default_factory=list)
    changes_applied: list[TuningChange] = Field(default_factory=list)
    changes_skipped: list[SkippedTuningChange] = Field(default_factory=list)


class TuningConfig(BaseModel):
    """Runtime tuning overrides for the decision engine.
    Written by TuningApplier; read by DecisionEngineService at startup."""

    version: int = 1
    updated_at: datetime
    overrides: dict[str, dict[str, object]] = Field(default_factory=dict)
    # e.g. {"observation_coverage": {"min_consecutive_runs": 1}}

    def get_int(self, family: str, key: str, default: int) -> int:
        val = self.overrides.get(family, {}).get(key)
        if isinstance(val, int):
            return val
        return default
