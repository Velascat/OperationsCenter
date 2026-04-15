from __future__ import annotations

import datetime
from enum import Enum
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class TriggerSource(str, Enum):
    DROP_FILE = "drop_file"
    PLANE_LABEL = "plane_label"
    QUEUE_DRAIN = "queue_drain"


class CampaignRecord(BaseModel):
    campaign_id: str
    slug: str
    spec_file: str
    area_keywords: list[str]
    status: Literal["active", "complete", "cancelled", "partial"]
    created_at: str
    last_progress_at: str | None = None
    spec_revision_count: int = 0
    trigger_source: str | None = None


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

    @classmethod
    def from_spec_text(cls, text: str) -> "SpecFrontMatter":
        """Parse YAML front matter from a spec document."""
        if not text.startswith("---"):
            raise ValueError("Spec text does not have YAML front matter")
        end = text.index("---", 3)
        front = text[3:end].strip()
        data = yaml.safe_load(front) or {}
        # Convert datetime objects (from YAML parsing) to ISO strings
        normalized = {
            k: v.isoformat() if isinstance(v, (datetime.datetime, datetime.date)) else v
            for k, v in data.items()
            if k in cls.model_fields
        }
        return cls(**normalized)
