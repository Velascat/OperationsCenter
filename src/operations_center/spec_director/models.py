# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import datetime
from enum import Enum
from typing import ClassVar, Literal

import yaml
from pydantic import BaseModel, Field


class TriggerSource(str, Enum):
    DROP_FILE = "drop_file"
    QUEUE_DRAIN = "queue_drain"


class CampaignRecord(BaseModel):
    campaign_id: str
    slug: str
    spec_file: str
    status: Literal["active", "complete", "cancelled", "partial"]
    created_at: str


class ActiveCampaigns(BaseModel):
    campaigns: list[CampaignRecord] = Field(default_factory=list)

    def active_campaigns(self) -> list[CampaignRecord]:
        return [c for c in self.campaigns if c.status == "active"]

    def has_active(self) -> bool:
        return any(c.status == "active" for c in self.campaigns)


class ComplianceInput(BaseModel):
    spec_text: str
    diff: str
    task_constraints: str
    task_phase: str
    spec_coverage_hint: str


class ComplianceVerdict(BaseModel):
    verdict: Literal["LGTM", "CONCERNS", "FAIL"]
    spec_coverage: float
    violations: list[str]
    notes: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class SpecFrontMatter(BaseModel):
    campaign_id: str
    slug: str
    phases: list[str] = Field(default_factory=list)
    repos: list[str] = Field(default_factory=list)
    area_keywords: list[str] = Field(default_factory=list)
    status: str = "active"
    created_at: str = ""

    # Human-friendly phase names → canonical task-mode names used in descriptions.
    # Specs written by hand (or by Claude) often use the short form; normalise so
    # campaign_builder always writes the correct `mode:` value into task descriptions.
    _PHASE_ALIASES: ClassVar[dict[str, str]] = {
        "test": "test_campaign",
        "improve": "improve_campaign",
    }

    @classmethod
    def from_spec_text(cls, text: str) -> "SpecFrontMatter":
        """Parse YAML front matter from a spec document."""
        if not (text.startswith("---") and len(text) > 3 and text[3] in ("\n", "\r", " ")):
            raise ValueError("Spec text does not have YAML front matter")
        try:
            end = text.index("---", 3)
        except ValueError:
            raise ValueError("Spec text is missing closing '---' for YAML front matter")
        front = text[3:end].strip()
        data = yaml.safe_load(front) or {}
        # Convert datetime objects (from YAML parsing) to ISO strings
        normalized = {
            k: v.isoformat() if isinstance(v, (datetime.datetime, datetime.date)) else v
            for k, v in data.items()
            if k in cls.model_fields
        }
        fm = cls(**normalized)
        # Normalise short-form phase names so campaign tasks get the correct mode.
        fm.phases = [cls._PHASE_ALIASES.get(p, p) for p in fm.phases]
        return fm
