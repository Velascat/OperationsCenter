# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


DECISION_ENGINE_VERSION = 1


class DecisionRepoRef(BaseModel):
    name: str
    path: Path


class CandidateRationale(BaseModel):
    matched_rules: list[str] = Field(default_factory=list)
    suppressed_by: list[str] = Field(default_factory=list)


class ProposalOutline(BaseModel):
    title_hint: str
    summary_hint: str
    labels_hint: list[str] = Field(default_factory=list)
    source_family: str | None = None


class EvidenceBundle(BaseModel):
    """Structured machine-readable evidence for a proposal candidate.

    Sits alongside the human-readable evidence_lines list. Populated automatically
    by CandidateBuilder for families whose evidence schema is stable (lint_fix,
    type_fix). Other families carry evidence only in the raw evidence dict.

    schema_version tracks the bundle format for forward-compatibility.
    """

    schema_version: int = 1
    kind: str  # e.g. "lint_count", "type_count"
    count: int | None = None          # total violations / errors
    distinct_file_count: int | None = None
    delta: int | None = None          # change from prior snapshot (positive = worsened)
    trend: str | None = None          # "present" | "worsening"
    top_codes: list[str] = Field(default_factory=list)
    source: str | None = None         # tool that produced the signal (ruff, ty, mypy)


class ProposalCandidate(BaseModel):
    candidate_id: str
    dedup_key: str
    family: str
    subject: str
    status: str = "emit"
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: str = "medium"
    evidence_lines: list[str] = Field(default_factory=list)
    risk_class: str = "logic"
    expires_after_runs: int = 5
    validation_profile: str = ""
    evidence_bundle: EvidenceBundle | None = None
    rationale: CandidateRationale
    proposal_outline: ProposalOutline


class SuppressedCandidate(BaseModel):
    dedup_key: str
    family: str
    subject: str
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ProposalCandidatesArtifact(BaseModel):
    run_id: str
    generated_at: datetime
    decision_engine_version: int = DECISION_ENGINE_VERSION
    source_command: str
    dry_run: bool = False
    repo: DecisionRepoRef
    source_insight_run_id: str
    candidates: list[ProposalCandidate] = Field(default_factory=list)
    suppressed: list[SuppressedCandidate] = Field(default_factory=list)
