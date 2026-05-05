# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R2 — RuntimeBinding → Kodo team-config binder.

Closes Kodo's G-001 from the OC side: until Kodo accepts a per-invocation
team override, OC translates the bound RuntimeBinding into the
appropriate team-config dict that Kodo already supports.

Translation table (RuntimeBinding kind/provider/model → Kodo team):

  kind=cli_subscription, provider=anthropic, model=opus
    → _CLAUDE_FALLBACK_TEAM (worker_smart=opus, worker_fast=sonnet)

  kind=cli_subscription, provider=anthropic, model=haiku
    → _OPUS_HAIKU_FALLBACK_TEAM (worker_smart=opus/haiku, worker_fast=haiku)

  kind=cli_subscription, provider=anthropic, model=sonnet (or unspecified)
    → _CLAUDE_FALLBACK_TEAM (sonnet via worker_smart fallback)

  kind=backend_default
    → None — let Kodo pick its own default

Anything else returns ``BindError`` so the caller can decide whether to
reject the request, fall back to backend default, or escalate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from operations_center.contracts.execution import RuntimeBindingSummary

# Mirror the team configs from operations_center.adapters.kodo.adapter so
# tests don't need to import the full adapter module (which pulls in
# subprocess + signal). Kept in sync via test_team_config_alignment.
_CLAUDE_FALLBACK_TEAM = {
    "agents": {
        "worker_fast":  {"backend": "claude", "model": "sonnet"},
        "worker_smart": {"backend": "claude", "model": "opus", "fallback_model": "sonnet"},
    }
}

_OPUS_HAIKU_FALLBACK_TEAM = {
    "agents": {
        "worker_fast":  {"backend": "claude", "model": "haiku"},
        "worker_smart": {"backend": "claude", "model": "opus", "fallback_model": "haiku"},
    }
}


class BindError(ValueError):
    """Raised when a RuntimeBinding cannot be translated to a Kodo team."""


@dataclass(frozen=True)
class KodoTeamSelection:
    """The result of binding a RuntimeBinding into Kodo team-config terms."""
    team_config: Optional[dict]    # None means "let Kodo use its default"
    label: str                     # human-readable selection name


def bind(rb: Optional[RuntimeBindingSummary]) -> KodoTeamSelection:
    """Translate a RuntimeBinding (or None) into a Kodo team selection.

    If ``rb`` is None, returns the backend-default selection.
    """
    if rb is None or rb.kind == "backend_default":
        return KodoTeamSelection(team_config=None, label="kodo_default")

    if rb.kind != "cli_subscription":
        raise BindError(
            f"Kodo only supports cli_subscription runtime bindings today; "
            f"got kind={rb.kind!r}. Update kodo/runtime_support.yaml or close G-001."
        )

    provider = (rb.provider or "anthropic").lower()
    if provider != "anthropic":
        raise BindError(
            f"Kodo team configs only model the anthropic provider; got provider={rb.provider!r}"
        )

    model = (rb.model or "").lower()
    if model in ("opus", "sonnet", ""):
        return KodoTeamSelection(
            team_config=_CLAUDE_FALLBACK_TEAM,
            label="claude_fallback_team",
        )
    if model == "haiku":
        return KodoTeamSelection(
            team_config=_OPUS_HAIKU_FALLBACK_TEAM,
            label="opus_haiku_fallback_team",
        )

    raise BindError(
        f"Kodo has no team config for model={rb.model!r}. "
        "Either extend kodo/binder.py or fall back to backend_default."
    )
