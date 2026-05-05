# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Catalog loader — discovers backends, validates, indexes.

Validation runs at load time and fails loudly on:
  - unknown CapabilitySet / RuntimeKind / SelectionMode values
  - audit_verdict.outcome=fork_required without a status:forked gap
  - audit_verdict.outcome=upstream_patch_pending without a gap with
    patch_deadline
  - audit_verdict.gap_refs that don't resolve in contract_gaps.yaml

Per the spec, this loader is the enforcement chokepoint and must run
in CI, at OC process startup, and before adapter registration.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from operations_center.executors._artifacts import (
    AuditArtifactError,
    AuditOutcome,
    AuditVerdict,
    CapabilityCard,
    ContractGap,
    GapStatus,
    RuntimeSupportCard,
    load_audit_verdict,
    load_capability_card,
    load_contract_gaps,
    load_runtime_support,
)
from operations_center.executors.decision import verdict_is_consistent


# Default executors directory; tests can pass their own.
_DEFAULT_EXECUTORS_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class BackendEntry:
    backend_id: str
    capability_card: CapabilityCard
    runtime_support: RuntimeSupportCard
    contract_gaps: list[ContractGap]
    audit_verdict: AuditVerdict


class CatalogValidationError(AuditArtifactError):
    """Raised when a backend's verdict violates the catalog enforcement rules."""


def _validate_verdict_against_gaps(
    backend_id: str,
    verdict: AuditVerdict,
    gaps_by_id: dict[str, ContractGap],
) -> None:
    # decision-matrix consistency (Phase 11 enforcement)
    ok, reason = verdict_is_consistent(verdict)
    if not ok:
        raise CatalogValidationError(f"{backend_id}: {reason}")

    # gap_refs must resolve
    missing = [gid for gid in verdict.gap_refs if gid not in gaps_by_id]
    if missing:
        raise CatalogValidationError(
            f"{backend_id}: audit_verdict.gap_refs {missing} not found in contract_gaps.yaml"
        )

    if verdict.outcome == AuditOutcome.FORK_REQUIRED:
        # require at least one referenced gap with status: forked
        has_forked = any(
            gaps_by_id[gid].status == GapStatus.FORKED for gid in verdict.gap_refs
        )
        if not has_forked:
            raise CatalogValidationError(
                f"{backend_id}: outcome=fork_required requires at least one "
                "referenced gap with status: forked"
            )

    if verdict.outcome == AuditOutcome.UPSTREAM_PATCH_PENDING:
        # require at least one open gap with a patch_deadline
        has_open_with_deadline = any(
            gaps_by_id[gid].status == GapStatus.OPEN and gaps_by_id[gid].patch_deadline
            for gid in verdict.gap_refs
        )
        if not has_open_with_deadline:
            raise CatalogValidationError(
                f"{backend_id}: outcome=upstream_patch_pending requires at least one "
                "referenced gap with status: open and patch_deadline set"
            )


@dataclass
class ExecutorCatalog:
    """In-memory index over loaded BackendEntry records."""

    entries: dict[str, BackendEntry]

    def all(self) -> Iterable[BackendEntry]:
        return self.entries.values()

    def get(self, backend_id: str) -> Optional[BackendEntry]:
        return self.entries.get(backend_id)


def load_catalog(executors_dir: Path | None = None) -> ExecutorCatalog:
    """Discover backends under ``executors_dir`` and load + validate each.

    A backend is any subdirectory containing all four artifact files:
    capability_card.yaml, runtime_support.yaml, contract_gaps.yaml,
    audit_verdict.yaml.
    """
    base = executors_dir or _DEFAULT_EXECUTORS_DIR
    entries: dict[str, BackendEntry] = {}
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if child.name in ("catalog", "samples"):
            continue
        cap_path = child / "capability_card.yaml"
        rs_path = child / "runtime_support.yaml"
        gaps_path = child / "contract_gaps.yaml"
        verdict_path = child / "audit_verdict.yaml"
        if not (cap_path.exists() and rs_path.exists()
                and gaps_path.exists() and verdict_path.exists()):
            continue
        cap = load_capability_card(cap_path)
        rs = load_runtime_support(rs_path)
        gaps = load_contract_gaps(gaps_path)
        verdict = load_audit_verdict(verdict_path)
        gaps_by_id = {g.id: g for g in gaps}
        _validate_verdict_against_gaps(child.name, verdict, gaps_by_id)
        entries[child.name] = BackendEntry(
            backend_id=child.name,
            capability_card=cap,
            runtime_support=rs,
            contract_gaps=gaps,
            audit_verdict=verdict,
        )
    return ExecutorCatalog(entries=entries)
