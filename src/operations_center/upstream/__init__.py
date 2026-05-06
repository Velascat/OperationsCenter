# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 — Fork-first upstream dependency management.

We never run upstream. Every dependency we'd otherwise pull from
upstream is registered here as a fork (even when zero patches are
applied) so installation, divergence, and reconciliation are all
first-class observable facts.

See docs/architecture/backend_control_audit.md (Phase 14).
"""
from operations_center.upstream.registry import (
    ForkEntry,
    ForkRegistry,
    InstallKind,
    InstallMode,
    RegistryError,
    load_registry,
)

__all__ = [
    "ForkEntry",
    "ForkRegistry",
    "InstallKind",
    "InstallMode",
    "RegistryError",
    "load_registry",
]
