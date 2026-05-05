# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Executor catalog v1 — in-memory truth source for SwitchBoard + OC.

Loads all per-backend artifacts (capability_card, runtime_support,
contract_gaps, audit_verdict), validates them against CxRP enums and
the catalog enforcement rules, builds an in-memory index, and answers
three hardcoded queries:

  - backends_supporting_runtime(runtime_kind)
  - backends_supporting_capabilities(required_capabilities)
  - backends_by_outcome(outcome)

NOT in v1: ranking, scoring, free-form query language, persistence,
subjective recommendations as routing input.

See docs/architecture/backend_control_audit.md (Phase 10).
"""
from operations_center.executors.catalog.loader import (
    BackendEntry,
    ExecutorCatalog,
    load_catalog,
)
from operations_center.executors.catalog.query import (
    backends_by_outcome,
    backends_supporting_capabilities,
    backends_supporting_runtime,
)

__all__ = [
    "BackendEntry",
    "ExecutorCatalog",
    "backends_by_outcome",
    "backends_supporting_capabilities",
    "backends_supporting_runtime",
    "load_catalog",
]
