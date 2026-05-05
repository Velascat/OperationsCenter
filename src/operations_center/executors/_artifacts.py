# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Per-backend audit artifact loaders + validators.

Loads + validates the four declarative YAML artifacts each backend
must ship:

  - contract_gaps.yaml      (Phase 7)
  - capability_card.yaml    (Phase 8)
  - runtime_support.yaml    (Phase 8)
  - audit_verdict.yaml      (Phase 9)

Validation runs at import-time of the catalog (Phase 10) and fails
loudly on:
  - unknown CapabilitySet members
  - unknown RuntimeKind / SelectionMode members
  - audit_verdict.outcome with missing gap_refs
  - upstream_patch_pending without a patch_deadline
  - fork_required without a forked-status gap
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml

from cxrp.vocabulary.capability import CapabilitySet
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

_CAPABILITY_VALUES = {c.value for c in CapabilitySet}
_RUNTIME_KIND_VALUES = {k.value for k in RuntimeKind}
_SELECTION_MODE_VALUES = {s.value for s in SelectionMode}


class AuditArtifactError(ValueError):
    """Raised when any backend artifact is malformed or violates a rule."""


# ── Phase 7: contract_gaps.yaml ─────────────────────────────────────────


class GapStatus(str, Enum):
    OPEN             = "open"
    MITIGATED        = "mitigated"
    PATCHED_UPSTREAM = "patched_upstream"
    FORKED           = "forked"


_GAP_REQUIRED = {"id", "gap", "discovered_at", "impact", "workaround", "fork_threshold", "status"}


@dataclass(frozen=True)
class ContractGap:
    id: str
    gap: str
    discovered_at: str
    backend_version: str
    impact: str
    workaround: str
    fork_threshold: str
    status: GapStatus
    patch_deadline: Optional[str] = None


def load_contract_gaps(path: Path) -> list[ContractGap]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if not isinstance(raw, list):
        raise AuditArtifactError(f"{path}: top-level must be a list")
    out: list[ContractGap] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise AuditArtifactError(f"{path}: each gap must be a dict")
        missing = _GAP_REQUIRED - set(entry)
        if missing:
            raise AuditArtifactError(f"{path}: gap missing fields: {sorted(missing)}")
        try:
            status = GapStatus(entry["status"])
        except ValueError as e:
            raise AuditArtifactError(f"{path}: invalid status {entry['status']!r}") from e
        out.append(ContractGap(
            id=str(entry["id"]),
            gap=str(entry["gap"]),
            discovered_at=str(entry["discovered_at"]),
            backend_version=str(entry.get("backend_version", "unknown")),
            impact=str(entry["impact"]),
            workaround=str(entry["workaround"]),
            fork_threshold=str(entry["fork_threshold"]),
            status=status,
            patch_deadline=str(entry["patch_deadline"]) if entry.get("patch_deadline") else None,
        ))
    return out


# ── Phase 8: capability_card.yaml ───────────────────────────────────────


@dataclass(frozen=True)
class CapabilityCard:
    backend_id: str
    backend_version: str
    advertised_capabilities: list[str]
    measured_constraints: dict[str, Any] = field(default_factory=dict)
    known_capability_gaps: list[str] = field(default_factory=list)


_CAP_CARD_DISALLOWED = {"good_for", "bad_for", "strengths", "weaknesses"}


def load_capability_card(path: Path) -> CapabilityCard:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise AuditArtifactError(f"{path}: must be a dict")
    leaked = _CAP_CARD_DISALLOWED & set(raw)
    if leaked:
        raise AuditArtifactError(
            f"{path}: subjective fields {sorted(leaked)} not allowed in capability_card.yaml; "
            "move them to recommendations.md"
        )
    advertised = list(raw.get("advertised_capabilities") or [])
    bad = [c for c in advertised if c not in _CAPABILITY_VALUES]
    if bad:
        raise AuditArtifactError(
            f"{path}: unknown CapabilitySet values: {bad}. Add to cxrp.vocabulary.capability "
            "or remove from card."
        )
    return CapabilityCard(
        backend_id=str(raw.get("backend_id", "")),
        backend_version=str(raw.get("backend_version", "unknown")),
        advertised_capabilities=advertised,
        measured_constraints=dict(raw.get("measured_constraints") or {}),
        known_capability_gaps=list(raw.get("known_capability_gaps") or []),
    )


# ── Phase 8: runtime_support.yaml ───────────────────────────────────────


@dataclass(frozen=True)
class RuntimeSupportCard:
    backend_id: str
    backend_version: str
    supported_runtime_kinds: list[str]
    supported_selection_modes: list[str]
    known_runtime_gaps: list[str] = field(default_factory=list)


def load_runtime_support(path: Path) -> RuntimeSupportCard:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise AuditArtifactError(f"{path}: must be a dict")
    kinds = list(raw.get("supported_runtime_kinds") or [])
    bad_kinds = [k for k in kinds if k not in _RUNTIME_KIND_VALUES]
    if bad_kinds:
        raise AuditArtifactError(f"{path}: unknown RuntimeKind values: {bad_kinds}")
    modes = list(raw.get("supported_selection_modes") or [])
    bad_modes = [m for m in modes if m not in _SELECTION_MODE_VALUES]
    if bad_modes:
        raise AuditArtifactError(f"{path}: unknown SelectionMode values: {bad_modes}")
    return RuntimeSupportCard(
        backend_id=str(raw.get("backend_id", "")),
        backend_version=str(raw.get("backend_version", "unknown")),
        supported_runtime_kinds=kinds,
        supported_selection_modes=modes,
        known_runtime_gaps=list(raw.get("known_runtime_gaps") or []),
    )


# ── Phase 9: audit_verdict.yaml ─────────────────────────────────────────


class PhaseClassification(str, Enum):
    PASS    = "PASS"
    PARTIAL = "PARTIAL"
    FAIL    = "FAIL"
    NA      = "N/A"


class AuditOutcome(str, Enum):
    ADAPTER_ONLY            = "adapter_only"
    ADAPTER_PLUS_WRAPPER    = "adapter_plus_wrapper"
    UPSTREAM_PATCH_PENDING  = "upstream_patch_pending"
    FORK_REQUIRED           = "fork_required"


_AUDIT_PHASES = (
    "runtime_control",
    "capability_control",
    "drift_detection",
    "failure_observability",
    "internal_routing",
)


@dataclass(frozen=True)
class AuditVerdict:
    backend_id: str
    audited_at: str
    audited_against_cxrp_version: str
    backend_version: str
    per_phase: dict[str, PhaseClassification]
    outcome: AuditOutcome
    gap_refs: list[str]
    next_review_by: Optional[str] = None


def load_audit_verdict(path: Path) -> AuditVerdict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise AuditArtifactError(f"{path}: must be a dict")

    per_phase_raw = raw.get("per_phase") or {}
    if not isinstance(per_phase_raw, dict):
        raise AuditArtifactError(f"{path}: per_phase must be a dict")
    per_phase: dict[str, PhaseClassification] = {}
    for phase in _AUDIT_PHASES:
        if phase not in per_phase_raw:
            raise AuditArtifactError(f"{path}: per_phase missing required phase {phase!r}")
        try:
            per_phase[phase] = PhaseClassification(per_phase_raw[phase])
        except ValueError as e:
            raise AuditArtifactError(
                f"{path}: per_phase.{phase} has invalid value {per_phase_raw[phase]!r}"
            ) from e

    try:
        outcome = AuditOutcome(raw["outcome"])
    except (KeyError, ValueError) as e:
        raise AuditArtifactError(f"{path}: invalid or missing outcome") from e

    return AuditVerdict(
        backend_id=str(raw.get("backend_id", "")),
        audited_at=str(raw.get("audited_at", "")),
        audited_against_cxrp_version=str(raw.get("audited_against_cxrp_version", "")),
        backend_version=str(raw.get("backend_version", "unknown")),
        per_phase=per_phase,
        outcome=outcome,
        gap_refs=list(raw.get("gap_refs") or []),
        next_review_by=str(raw["next_review_by"]) if raw.get("next_review_by") else None,
    )
