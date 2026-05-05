# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""V1 catalog queries — three only.

No ranking, no scoring, no DSL. Each query takes the catalog + a filter
and returns matching backend_ids in deterministic (sorted) order.
"""
from __future__ import annotations

from typing import Iterable

from operations_center.executors._artifacts import AuditOutcome
from operations_center.executors.catalog.loader import ExecutorCatalog


def backends_supporting_runtime(
    catalog: ExecutorCatalog,
    *,
    runtime_kind: str,
) -> list[str]:
    """Return backend_ids whose runtime_support.yaml lists this kind."""
    return sorted(
        e.backend_id
        for e in catalog.all()
        if runtime_kind in e.runtime_support.supported_runtime_kinds
    )


def backends_supporting_capabilities(
    catalog: ExecutorCatalog,
    *,
    required_capabilities: Iterable[str],
) -> list[str]:
    """Return backend_ids whose advertised_capabilities ⊇ required_capabilities."""
    required = set(required_capabilities)
    return sorted(
        e.backend_id
        for e in catalog.all()
        if required.issubset(set(e.capability_card.advertised_capabilities))
    )


def backends_by_outcome(
    catalog: ExecutorCatalog,
    *,
    outcome: str | AuditOutcome,
) -> list[str]:
    """Return backend_ids whose audit_verdict.outcome matches."""
    target = outcome.value if isinstance(outcome, AuditOutcome) else outcome
    return sorted(
        e.backend_id
        for e in catalog.all()
        if e.audit_verdict.outcome.value == target
    )
