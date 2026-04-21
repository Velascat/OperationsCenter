# tests/test_switchboard_routing.py
"""Tests verifying that spec_director services route through SwitchBoard.

These tests prove the integration contract:
  call_claude() → SwitchBoardClient.complete() when SWITCHBOARD_URL is set.
  call_claude() → Claude CLI subprocess when SWITCHBOARD_URL is absent.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# call_claude routing
# ---------------------------------------------------------------------------


def test_call_claude_uses_switchboard_when_url_set(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    with patch(
        "control_plane.adapters.switchboard.client.httpx.post"
    ) as mock_post:
        mock_post.return_value = _openai_response("from switchboard")

        from control_plane.spec_director._claude_cli import call_claude
        result = call_claude("hello", model="claude-sonnet-4-6")

    assert result == "from switchboard"
    mock_post.assert_called_once()


def test_call_claude_uses_cli_when_no_url(monkeypatch):
    monkeypatch.delenv("SWITCHBOARD_URL", raising=False)

    with patch("control_plane.spec_director._claude_cli._call_claude_cli") as mock_cli:
        mock_cli.return_value = "from cli"

        from control_plane.spec_director._claude_cli import call_claude
        result = call_claude("hello", model="claude-sonnet-4-6")

    assert result == "from cli"
    mock_cli.assert_called_once()


def test_call_claude_sends_system_prompt_as_message(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    captured_messages = []

    def capture_post(url, *, json, headers, timeout):
        captured_messages.extend(json["messages"])
        return _openai_response("ok")

    with patch("control_plane.adapters.switchboard.client.httpx.post", side_effect=capture_post):
        from control_plane.spec_director._claude_cli import call_claude
        call_claude("user content", system_prompt="be concise", model="claude-sonnet-4-6")

    roles = [m["role"] for m in captured_messages]
    assert roles[0] == "system"
    assert roles[-1] == "user"
    assert captured_messages[0]["content"] == "be concise"


def test_call_claude_without_system_prompt_sends_user_only(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    captured = []

    def capture_post(url, *, json, headers, timeout):
        captured.extend(json["messages"])
        return _openai_response("ok")

    with patch("control_plane.adapters.switchboard.client.httpx.post", side_effect=capture_post):
        from control_plane.spec_director._claude_cli import call_claude
        call_claude("my prompt")

    assert len(captured) == 1
    assert captured[0]["role"] == "user"


# ---------------------------------------------------------------------------
# BrainstormService routes through SwitchBoard
# ---------------------------------------------------------------------------

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
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Add Webhook Ingestion

## Overview
Test spec.
"""


def test_brainstorm_service_routes_through_switchboard(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    with patch("control_plane.adapters.switchboard.client.httpx.post") as mock_post:
        mock_post.return_value = _openai_response(_FAKE_SPEC)

        from control_plane.spec_director.brainstorm import BrainstormService
        from control_plane.spec_director.context_bundle import ContextBundle

        svc = BrainstormService(model="claude-opus-4-6")
        bundle = ContextBundle(
            git_logs={"MyRepo": "abc fix auth"},
            specs_index=[],
            recent_done_tasks=[],
            recent_cancelled_tasks=[],
            open_task_count=0,
            seed_text="add webhooks",
            available_repos=["MyRepo"],
        )
        result = svc.brainstorm(bundle)

    assert result.slug == "add-webhook-ingestion"
    mock_post.assert_called_once()

    # Verify the SwitchBoard-specific headers were sent
    headers = mock_post.call_args.kwargs["headers"]
    assert "X-Request-ID" in headers
    assert headers["X-SwitchBoard-Profile"] == "capable"  # opus → capable
    assert headers["X-SwitchBoard-Tenant-ID"] == "control-plane"


def test_brainstorm_service_decision_visible_via_request_id(monkeypatch):
    """Verifies that a request_id is always present on the SwitchBoard call — making
    the decision retrievable via GET /admin/decisions/{request_id}."""
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    request_ids = []

    def capture(url, *, json, headers, timeout):
        request_ids.append(headers.get("X-Request-ID"))
        return _openai_response(_FAKE_SPEC)

    with patch("control_plane.adapters.switchboard.client.httpx.post", side_effect=capture):
        from control_plane.spec_director.brainstorm import BrainstormService
        from control_plane.spec_director.context_bundle import ContextBundle

        svc = BrainstormService(model="claude-opus-4-6")
        svc.brainstorm(ContextBundle(
            git_logs={}, specs_index=[], recent_done_tasks=[], recent_cancelled_tasks=[],
            open_task_count=0, seed_text="test", available_repos=["r"],
        ))

    assert request_ids[0] is not None
    assert len(request_ids[0]) > 0


# ---------------------------------------------------------------------------
# SpecComplianceService routes through SwitchBoard
# ---------------------------------------------------------------------------

_FAKE_COMPLIANCE_JSON = '{"verdict": "LGTM", "spec_coverage": 0.9, "violations": [], "notes": "ok"}'


def test_compliance_service_routes_through_switchboard(monkeypatch):
    monkeypatch.setenv("SWITCHBOARD_URL", "http://localhost:20401")

    with patch("control_plane.adapters.switchboard.client.httpx.post") as mock_post:
        mock_post.return_value = _openai_response(_FAKE_COMPLIANCE_JSON)

        from control_plane.spec_director.compliance import SpecComplianceService
        from control_plane.spec_director.models import ComplianceInput

        svc = SpecComplianceService(model="claude-sonnet-4-6")
        inp = ComplianceInput(
            spec_text="# Spec\n## Goals\n1. Add auth",
            diff="+ def authenticate(): pass",
            task_constraints="Only modify src/auth/",
            task_phase="implement",
            spec_coverage_hint="Goal 1",
        )
        verdict = svc.check(inp)

    assert verdict.verdict == "LGTM"
    mock_post.assert_called_once()

    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-SwitchBoard-Profile"] == "capable"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _openai_response(content: str):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }
    mock.status_code = 200
    mock.text = ""
    return mock
