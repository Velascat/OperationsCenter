# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R2 — Kodo binder tests."""
from __future__ import annotations

import pytest

from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.executors.kodo.binder import (
    BindError,
    KodoTeamSelection,
    _CLAUDE_FALLBACK_TEAM,
    _OPUS_HAIKU_FALLBACK_TEAM,
    bind,
)


def _rb(**overrides) -> RuntimeBindingSummary:
    base = {
        "kind": "cli_subscription",
        "selection_mode": "explicit_request",
        "provider": "anthropic",
        "model": "opus",
    }
    base.update(overrides)
    return RuntimeBindingSummary(**base)


class TestBind:
    def test_none_returns_backend_default(self):
        sel = bind(None)
        assert sel.team_config is None
        assert sel.label == "kodo_default"

    def test_backend_default_kind_returns_default(self):
        # backend_default kind only legal with backend_default selection_mode
        sel = bind(RuntimeBindingSummary(
            kind="backend_default", selection_mode="backend_default",
        ))
        assert sel.team_config is None

    def test_opus_picks_claude_fallback_team(self):
        sel = bind(_rb(model="opus"))
        assert sel.team_config is _CLAUDE_FALLBACK_TEAM
        assert sel.label == "claude_fallback_team"

    def test_sonnet_picks_claude_fallback_team(self):
        sel = bind(_rb(model="sonnet"))
        assert sel.team_config is _CLAUDE_FALLBACK_TEAM

    def test_haiku_picks_opus_haiku_fallback(self):
        sel = bind(_rb(model="haiku"))
        assert sel.team_config is _OPUS_HAIKU_FALLBACK_TEAM
        assert sel.label == "opus_haiku_fallback_team"

    def test_unknown_model_raises_bind_error(self):
        with pytest.raises(BindError, match="no team config"):
            bind(_rb(model="some_future_model"))

    def test_non_cli_kind_raises_bind_error(self):
        rb = RuntimeBindingSummary(
            kind="hosted_api", selection_mode="explicit_request",
            provider="anthropic", model="opus", endpoint="https://x",
        )
        with pytest.raises(BindError, match="cli_subscription"):
            bind(rb)

    def test_non_anthropic_provider_raises_bind_error(self):
        rb = RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="openai", model="gpt-4",
        )
        with pytest.raises(BindError, match="anthropic"):
            bind(rb)


# Removed: test_team_config_alignment_with_kodo_adapter — the adapter
# no longer carries fallback team configs (the dead-fallback code was
# cut). The binder's team configs are now the canonical source and
# don't need to mirror anything.
