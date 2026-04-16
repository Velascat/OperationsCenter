# tests/spec_director/test_brainstorm.py
from __future__ import annotations
from unittest.mock import patch
import pytest


_FAKE_SPEC = """---
campaign_id: test-uuid-1234
slug: add-webhook-ingestion
phases:
  - implement
  - test
repos:
  - MyRepo
area_keywords:
  - src/ingestion/
  - webhook
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Add Webhook Ingestion

## Overview
Add HTTP webhook endpoint to receive events.
"""


@patch("control_plane.spec_director.brainstorm.call_claude", return_value=_FAKE_SPEC)
def test_brainstorm_returns_spec_text_and_front_matter(mock_call):
    from control_plane.spec_director.brainstorm import BrainstormService
    from control_plane.spec_director.context_bundle import ContextBundle

    service = BrainstormService(model="claude-opus-4-6")
    bundle = ContextBundle(
        insight_snapshot="{}",
        git_log="abc123 fix auth",
        specs_index=[],
        board_summary=[],
        seed_text="add webhook ingestion",
    )
    result = service.brainstorm(bundle)
    assert result.spec_text.startswith("---")
    assert result.slug == "add-webhook-ingestion"
    assert "implement" in result.phases
    assert result.prompt_tokens == 0


@patch("control_plane.spec_director.brainstorm.call_claude", return_value="# No front matter here\nJust text")
def test_brainstorm_raises_on_missing_front_matter(mock_call):
    from control_plane.spec_director.brainstorm import BrainstormService, BrainstormError
    from control_plane.spec_director.context_bundle import ContextBundle

    service = BrainstormService(model="claude-opus-4-6")
    bundle = ContextBundle(insight_snapshot="", git_log="", specs_index=[], board_summary=[], seed_text="")
    with pytest.raises(BrainstormError):
        service.brainstorm(bundle)
