# tests/spec_director/test_brainstorm.py
from __future__ import annotations
from unittest.mock import MagicMock, patch
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


def test_brainstorm_returns_spec_text_and_front_matter():
    from control_plane.spec_director.brainstorm import BrainstormService
    from control_plane.spec_director.context_bundle import ContextBundle

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=_FAKE_SPEC)]
    mock_message.usage.input_tokens = 500
    mock_message.usage.output_tokens = 200
    mock_client.messages.create.return_value = mock_message

    service = BrainstormService(client=mock_client, model="claude-opus-4-6")
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
    assert result.prompt_tokens == 500


def test_brainstorm_raises_on_missing_front_matter():
    from control_plane.spec_director.brainstorm import BrainstormService, BrainstormError
    from control_plane.spec_director.context_bundle import ContextBundle

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="# No front matter here\nJust text")]
    mock_message.usage.input_tokens = 100
    mock_message.usage.output_tokens = 50
    mock_client.messages.create.return_value = mock_message

    service = BrainstormService(client=mock_client, model="claude-opus-4-6")
    bundle = ContextBundle(insight_snapshot="", git_log="", specs_index=[], board_summary=[], seed_text="")
    with pytest.raises(BrainstormError):
        service.brainstorm(bundle)
