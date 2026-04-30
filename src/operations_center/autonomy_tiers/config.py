# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

_DEFAULT_TIERS_PATH = Path("config/autonomy_tiers.json")

# Default tier for each family when no explicit override is set.
# Tier 1 = auto-create in Backlog (human must promote to run).
# Tier 2 = auto-create in Ready for AI (executes immediately).
# Tier 0 = do not auto-create (decision artifact only).
_DEFAULT_FAMILY_TIERS: dict[str, int] = {
    # style families auto-execute by default
    "lint_fix": 2,
    "todo_accumulation": 1,  # style risk but new family; keep at 1 until track record shows
    # observability families auto-execute — read-only / additive coverage work,
    # no production-runtime behavior change
    "observation_coverage": 2,
    "test_visibility": 2,
    "dependency_drift_followup": 2,
    # logic families require human promotion
    "execution_health_followup": 1,
    "backlog_promotion": 1,
    "type_fix": 1,  # logic risk; keep at 1 until track record shows safe to auto-run
    "ci_pattern": 1,  # logic risk; requires human review before auto-executing
    "validation_pattern_followup": 1,  # logic risk; investigation required before executing
    # structural and arch require explicit human approval
    "hotspot_concentration": 1,
    "arch_promotion": 0,
}


class AutonomyTiersConfig(BaseModel):
    version: int = 1
    updated_at: datetime
    overrides: dict[str, int] = Field(default_factory=dict)
    notes: dict[str, str] = Field(default_factory=dict)


def load_tiers_config(path: Path | None = None) -> AutonomyTiersConfig | None:
    p = path or _DEFAULT_TIERS_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return AutonomyTiersConfig.model_validate(data)
    except Exception:
        return None


def save_tiers_config(config: AutonomyTiersConfig, path: Path | None = None) -> None:
    p = path or _DEFAULT_TIERS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(config.model_dump_json(indent=2))


def get_family_tier(family: str, config: AutonomyTiersConfig | None) -> int:
    """Return the effective tier for a family, respecting config overrides."""
    if config is not None and family in config.overrides:
        return config.overrides[family]
    return _DEFAULT_FAMILY_TIERS.get(family, 1)
