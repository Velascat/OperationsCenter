# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Propagation policy — when does a contract change auto-fire downstream tasks?

Operators express intent at three layers:

1. **Global enable** — `PropagationSettings.enabled`. False by default;
   no propagation happens until explicitly turned on.
2. **Per-edge-type** — `auto_trigger_edge_types`. Only edge types in this
   set drive automatic propagation. Default: empty (manual-only).
3. **Per-(target, consumer) override** — `overrides[(target, consumer)]`
   lets operators trust specific pairs (auto-promote to Ready for AI)
   or block specific pairs (suppress entirely).

Policy is read-only: the propagator asks `policy.decide(...)` and gets
back a structured `PropagationDecision`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Bundled default — operator can override via PropagationSettings.dedup_window_hours.
DEFAULT_DEDUP_WINDOW_HOURS: int = 24


class _Action(str, Enum):
    SKIP = "skip"               # don't fire any task
    BACKLOG = "backlog"         # create a task in Backlog (default safe path)
    READY_FOR_AI = "ready_for_ai"  # create + promote to Ready for AI (trusted pair)


@dataclass(frozen=True)
class PropagationDecision:
    """Result of consulting policy for a single (target, consumer) pair."""

    action: _Action
    reason: str

    def fires_task(self) -> bool:
        return self.action is not _Action.SKIP


@dataclass(frozen=True)
class _PairOverride:
    """Per-(target_repo_id, consumer_repo_id) override."""

    action: _Action
    reason: str = "operator override"


@dataclass(frozen=True)
class PropagationSettings:
    """Operator-authored configuration for the whole propagation engine.

    Loaded from `Settings.contract_change_propagation`. Defaults are
    safety-first: nothing fires until an operator opts in.
    """

    enabled: bool = False
    auto_trigger_edge_types: frozenset[str] = field(default_factory=frozenset)
    dedup_window_hours: int = DEFAULT_DEDUP_WINDOW_HOURS
    pair_overrides: tuple[tuple[str, str, _PairOverride], ...] = ()


@dataclass(frozen=True)
class PropagationPolicy:
    """The decision-making layer. Wraps `PropagationSettings` with logic."""

    settings: PropagationSettings

    @classmethod
    def disabled(cls) -> "PropagationPolicy":
        """Construct a never-fires policy. Useful for tests + the off-state."""
        return cls(settings=PropagationSettings(enabled=False))

    def decide(
        self,
        *,
        target_repo_id: str,
        consumer_repo_id: str,
        edge_type: str,
    ) -> PropagationDecision:
        """Decide what to do for one (target, consumer) pair.

        Order:
        1. If `settings.enabled` is False → SKIP (global off)
        2. If a pair override is set → its action wins
        3. If `edge_type` is in `auto_trigger_edge_types` → BACKLOG
        4. Otherwise → SKIP (no edge-type opt-in)
        """
        if not self.settings.enabled:
            return PropagationDecision(action=_Action.SKIP, reason="propagation disabled globally")

        for ti, ci, override in self.settings.pair_overrides:
            if ti == target_repo_id and ci == consumer_repo_id:
                return PropagationDecision(action=override.action, reason=override.reason)

        if edge_type in self.settings.auto_trigger_edge_types:
            return PropagationDecision(
                action=_Action.BACKLOG,
                reason=f"edge_type {edge_type!r} is auto-trigger",
            )

        return PropagationDecision(
            action=_Action.SKIP,
            reason=f"edge_type {edge_type!r} not in auto_trigger_edge_types",
        )


__all__ = [
    "DEFAULT_DEDUP_WINDOW_HOURS",
    "PropagationDecision",
    "PropagationPolicy",
    "PropagationSettings",
    "_Action",
    "_PairOverride",
]
