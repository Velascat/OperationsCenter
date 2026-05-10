# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""R5 — OC startup hook for the executor catalog.

Spec: Phase 12 enforcement requires catalog validation to run at OC
process startup, in CI, and before adapter registration completes.

This module provides ``initialize_catalog()`` for any OC entrypoint
that wants to fail-fast on invalid backend artifacts. CLI entrypoints
should call it during their startup sequence; web servers from their
lifespan/startup event.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from operations_center.executors.catalog import ExecutorCatalog, load_catalog

logger = logging.getLogger(__name__)


def initialize_catalog(
    executors_dir: Path | None = None,
    *,
    fail_fast: bool = True,
) -> Optional[ExecutorCatalog]:
    """Load and validate the executor catalog at process startup.

    Args:
        executors_dir: Override the default ``operations_center/executors/``
            location. None = use the package default.
        fail_fast: When True (default), validation errors raise
            ``CatalogValidationError`` and abort startup. When False,
            errors are logged and the function returns None — useful
            for advisory tools that should not crash on bad cards.

    Returns:
        The loaded ``ExecutorCatalog`` on success; None when fail_fast=False
        and validation failed.
    """
    try:
        catalog = load_catalog(executors_dir)
        logger.info(
            "Executor catalog loaded: %d backend(s) — %s",
            len(catalog.entries),
            sorted(catalog.entries),
        )
        return catalog
    except Exception:
        logger.exception("Executor catalog initialization failed")
        if fail_fast:
            raise
        return None
