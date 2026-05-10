# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


INSIGHT_ENGINE_VERSION = 1


class InsightRepoRef(BaseModel):
    name: str
    path: Path


class SourceSnapshotRef(BaseModel):
    run_id: str
    observed_at: datetime


class DerivedInsight(BaseModel):
    insight_id: str
    dedup_key: str
    kind: str
    subject: str
    status: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    first_seen_at: datetime
    last_seen_at: datetime


class RepoInsightsArtifact(BaseModel):
    run_id: str
    generated_at: datetime
    insight_engine_version: int = INSIGHT_ENGINE_VERSION
    source_command: str
    repo: InsightRepoRef
    source_snapshots: list[SourceSnapshotRef] = Field(default_factory=list)
    insights: list[DerivedInsight] = Field(default_factory=list)
