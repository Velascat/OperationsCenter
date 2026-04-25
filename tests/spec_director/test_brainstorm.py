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


@patch("operations_center.spec_director.brainstorm.call_claude", return_value=_FAKE_SPEC)
def test_brainstorm_returns_spec_text_and_front_matter(mock_call):
    from operations_center.spec_director.brainstorm import BrainstormService
    from operations_center.spec_director.context_bundle import ContextBundle

    service = BrainstormService(model="claude-opus-4-6")
    bundle = ContextBundle(
        git_logs={"main_repo": "abc123 fix auth"},
        specs_index=[],
        recent_done_tasks=[],
        recent_cancelled_tasks=[],
        open_task_count=0,
        seed_text="add webhook ingestion",
        available_repos=["main_repo"],
    )
    result = service.brainstorm(bundle)
    assert result.spec_text.startswith("---")
    assert result.slug == "add-webhook-ingestion"
    assert "implement" in result.phases
    assert result.prompt_tokens == 0


@patch("operations_center.spec_director.brainstorm.call_claude", return_value="# No front matter here\nJust text")
def test_brainstorm_raises_on_missing_front_matter(mock_call):
    from operations_center.spec_director.brainstorm import BrainstormService, BrainstormError
    from operations_center.spec_director.context_bundle import ContextBundle

    service = BrainstormService(model="claude-opus-4-6")
    bundle = ContextBundle(
        git_logs={},
        specs_index=[],
        recent_done_tasks=[],
        recent_cancelled_tasks=[],
        open_task_count=0,
        seed_text="",
        available_repos=[],
    )
    with pytest.raises(BrainstormError):
        service.brainstorm(bundle)
