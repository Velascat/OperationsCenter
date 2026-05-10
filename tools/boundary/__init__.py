# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Boundary audit utilities (ER-000 Phase 0 freeze).

Lightweight, dependency-free checks that pin cross-repo boundaries before
new platform primitives (ER-001 repo graph, ER-002 run memory, ER-003
lifecycle, ER-004 swarm) land. Forward-looking by design: the denylists
include symbol names that do not yet exist so the check fails closed if
those primitives accidentally collapse into SwitchBoard.
"""

from .switchboard_denylist import (
    BoundaryFinding,
    DEFAULT_SWITCHBOARD_DENYLIST,
    check_switchboard_denylist,
)

__all__ = [
    "BoundaryFinding",
    "DEFAULT_SWITCHBOARD_DENYLIST",
    "check_switchboard_denylist",
]
