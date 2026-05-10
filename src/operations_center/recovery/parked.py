# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Formal parked-state behavior for the oversight loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class ParkedState:
    root_cause_signature: str
    parked_reason: str
    parked_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    unchanged_cycles: int = 0
    last_evidence_hash: str | None = None
    unpark_conditions: tuple[str, ...] = (
        "backend_health_change",
        "queue_change",
        "runtime_config_change",
        "watcher_state_change",
        "execution_outcome_change",
    )


@dataclass(frozen=True)
class ParkedStateDecision:
    parked: bool
    reason: str
    unchanged_cycles: int = 0


def should_unpark(
    parked: ParkedState,
    *,
    current_evidence_hash: str,
    triggered_conditions: tuple[str, ...] = (),
) -> ParkedStateDecision:
    if current_evidence_hash != parked.last_evidence_hash:
        return ParkedStateDecision(parked=False, reason="semantic evidence changed")
    matched = sorted(set(triggered_conditions).intersection(parked.unpark_conditions))
    if matched:
        return ParkedStateDecision(
            parked=False,
            reason=f"unpark condition met: {','.join(matched)}",
        )
    return ParkedStateDecision(
        parked=True,
        reason="no unpark condition met",
        unchanged_cycles=parked.unchanged_cycles + 1,
    )
