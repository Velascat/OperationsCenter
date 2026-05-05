# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 13 — re-audit trigger logic.

Re-audit if ANY of:
  - backend version changed
  - CxRP RuntimeBinding schema changed
  - CxRP CapabilitySet schema changed
  - audited_against_cxrp_version < current minor version
  - audit > 90 days old AND backend invoked in last 30 days

The CxRP-version trigger overlaps with the schema-change triggers in
most real bumps. The redundancy is intentional belt-and-suspenders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

from operations_center.executors._artifacts import AuditVerdict


class ReauditReason(str, Enum):
    BACKEND_VERSION_CHANGED        = "backend_version_changed"
    RUNTIMEBINDING_SCHEMA_CHANGED  = "runtimebinding_schema_changed"
    CAPABILITYSET_SCHEMA_CHANGED   = "capabilityset_schema_changed"
    CXRP_MINOR_VERSION_ADVANCED    = "cxrp_minor_version_advanced"
    STALE_AND_RECENTLY_INVOKED     = "stale_and_recently_invoked"


@dataclass(frozen=True)
class ReauditDecision:
    needed: bool
    reasons: tuple[ReauditReason, ...]


def _parse_iso_date(value: str) -> date:
    if "T" in value:
        return datetime.fromisoformat(value).date()
    return date.fromisoformat(value)


def _minor_version(version: str) -> tuple[int, int]:
    parts = version.split(".")
    if len(parts) < 2:
        return (0, 0)
    return (int(parts[0]), int(parts[1]))


def needs_reaudit(
    verdict: AuditVerdict,
    *,
    current_backend_version: str,
    current_cxrp_version: str,
    runtimebinding_schema_changed: bool,
    capabilityset_schema_changed: bool,
    last_invoked_at: Optional[date] = None,
    today: Optional[date] = None,
) -> ReauditDecision:
    """Compute the re-audit decision for one backend's verdict."""
    today = today or date.today()
    reasons: list[ReauditReason] = []

    if (
        verdict.backend_version != "unknown"
        and current_backend_version != "unknown"
        and verdict.backend_version != current_backend_version
    ):
        reasons.append(ReauditReason.BACKEND_VERSION_CHANGED)

    if runtimebinding_schema_changed:
        reasons.append(ReauditReason.RUNTIMEBINDING_SCHEMA_CHANGED)
    if capabilityset_schema_changed:
        reasons.append(ReauditReason.CAPABILITYSET_SCHEMA_CHANGED)

    if _minor_version(verdict.audited_against_cxrp_version) < _minor_version(current_cxrp_version):
        reasons.append(ReauditReason.CXRP_MINOR_VERSION_ADVANCED)

    try:
        audited_at = _parse_iso_date(verdict.audited_at)
    except (ValueError, TypeError):
        audited_at = None
    if audited_at is not None and last_invoked_at is not None:
        is_stale = (today - audited_at) > timedelta(days=90)
        recently_invoked = (today - last_invoked_at) <= timedelta(days=30)
        if is_stale and recently_invoked:
            reasons.append(ReauditReason.STALE_AND_RECENTLY_INVOKED)

    return ReauditDecision(needed=bool(reasons), reasons=tuple(reasons))
