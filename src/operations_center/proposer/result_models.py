# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


PROPOSER_INTEGRATION_VERSION = 1


class ProposerRepoRef(BaseModel):
    name: str
    path: Path


class CreatedProposalResult(BaseModel):
    candidate_id: str
    dedup_key: str
    family: str
    plane_issue_id: str | None = None
    plane_title: str
    status: str


class SkippedProposalResult(BaseModel):
    candidate_id: str
    dedup_key: str
    family: str
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class FailedProposalResult(BaseModel):
    candidate_id: str
    dedup_key: str
    family: str
    reason: str
    error: str


class ProposalResultsArtifact(BaseModel):
    run_id: str
    generated_at: datetime
    proposer_integration_version: int = PROPOSER_INTEGRATION_VERSION
    source_command: str
    repo: ProposerRepoRef
    source_decision_run_id: str
    dry_run: bool = False
    created: list[CreatedProposalResult] = Field(default_factory=list)
    skipped: list[SkippedProposalResult] = Field(default_factory=list)
    failed: list[FailedProposalResult] = Field(default_factory=list)
