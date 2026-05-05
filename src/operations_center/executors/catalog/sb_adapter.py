# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""SwitchBoard ExecutorCatalog port adapter.

OC's catalog is the truth source. SB defines a port (3 query methods)
and depends on OC to supply an implementation. This adapter wraps the
loaded ExecutorCatalog so it satisfies the SB port without SB needing
to import OC code.
"""
from __future__ import annotations

from typing import Iterable

from operations_center.executors.catalog.loader import ExecutorCatalog as OcCatalog
from operations_center.executors.catalog.query import (
    backends_by_outcome,
    backends_supporting_capabilities,
    backends_supporting_runtime,
)


class SwitchboardCatalogAdapter:
    """Implements switchboard.ports.executor_catalog.ExecutorCatalog."""

    def __init__(self, catalog: OcCatalog) -> None:
        self._catalog = catalog

    def backends_supporting_runtime(self, *, runtime_kind: str) -> list[str]:
        return backends_supporting_runtime(self._catalog, runtime_kind=runtime_kind)

    def backends_supporting_capabilities(
        self, *, required_capabilities: Iterable[str]
    ) -> list[str]:
        return backends_supporting_capabilities(
            self._catalog, required_capabilities=required_capabilities,
        )

    def backends_by_outcome(self, *, outcome: str) -> list[str]:
        return backends_by_outcome(self._catalog, outcome=outcome)
