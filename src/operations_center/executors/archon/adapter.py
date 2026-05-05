# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Archon discovery adapter — Phase 1.

Wraps the existing operations_center.backends.archon invocation logic
with sample capture. NO normalization (Phase 2). NO policy enforcement.
NO RuntimeBinding pass-through yet — that's Phase 3+.

KNOWN GAP (will become contract_gaps.yaml entry G-001 after first real
discovery run): Archon's current adapter has no per-request runtime
parameter. This Phase 1 harness captures whatever Archon decides to do
internally; the divergence between bound runtime (which doesn't exist
yet) and observed runtime is what the audit will measure.
"""
from __future__ import annotations

# Re-use the kodo discovery primitives — the per-backend split is
# directories + samples + cards, not the discovery harness itself.
from operations_center.executors.kodo.adapter import (
    DiscoveryRunCapture,
    discover as _discover,
    write_capture,
)
from pathlib import Path
from typing import Any, Optional

_SAMPLES_BASE = Path(__file__).parent / "samples"


def discover(
    *,
    lane: str,
    invocation: dict[str, Any],
    raw_output: dict[str, Any],
    duration_seconds: Optional[float] = None,
    extras: Optional[dict[str, Any]] = None,
) -> DiscoveryRunCapture:
    return _discover(
        lane=lane,
        invocation=invocation,
        raw_output=raw_output,
        duration_seconds=duration_seconds,
        extras=extras,
        samples_base=_SAMPLES_BASE,
    )
