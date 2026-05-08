# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Cross-repo task chaining — propagation library.

When a contract repo (CxRP, RxP, PlatformManifest, or a project's
own contract repo) changes, downstream consumers may need re-validation
runs (Custodian sweeps, integration tests, etc.). The propagation
library wraps that pattern.

Public API (R5.1 — library only; R5.2 ships the operator entrypoint):

    PropagationPolicy   — per-edge-type / per-(target,consumer) rules
    PropagationRegistry — task templates per (target, consumer)
    PropagationDedupStore — JSON sidecar in state/ for idempotency
    ContractChangePropagator — orchestrator that walks the impact set
    PropagationRecord — the structured artifact every action emits

Mandatory: every call to ``propagator.propagate()`` writes a
``PropagationRecord`` to ``state/propagation/`` regardless of whether
any tasks were created. This is the observability floor — operators
must always be able to answer "why did/didn't a propagation fire?"
without re-running anything.

Defaults are operator-friendly:
    - All consumer tasks land in Backlog (not Ready for AI)
    - Per-(target, consumer) opt-in for auto-promotion
    - 24h dedup window per (target, consumer, version)
    - Disabled-by-default until operator authors a PropagationPolicy
"""

from .dedup import DedupKey, PropagationDedupStore
from .links import PARENT_LINK_TEMPLATE, ParentLink, format_parent_link
from .policy import (
    DEFAULT_DEDUP_WINDOW_HOURS,
    PropagationDecision,
    PropagationPolicy,
    PropagationSettings,
)
from .propagator import (
    ContractChangePropagator,
    PropagationOutcome,
    PropagationRecord,
)
from .registry import PropagationRegistry, TaskTemplate

__all__ = [
    "DEFAULT_DEDUP_WINDOW_HOURS",
    "ContractChangePropagator",
    "DedupKey",
    "PARENT_LINK_TEMPLATE",
    "ParentLink",
    "PropagationDecision",
    "PropagationDedupStore",
    "PropagationOutcome",
    "PropagationPolicy",
    "PropagationRecord",
    "PropagationRegistry",
    "PropagationSettings",
    "TaskTemplate",
    "format_parent_link",
]
