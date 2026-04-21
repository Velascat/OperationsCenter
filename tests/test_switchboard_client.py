# tests/test_switchboard_client.py
"""Unit tests for SwitchBoardClient."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from control_plane.adapters.switchboard.client import (
    SwitchBoardClient,
    SwitchBoardError,
    _resolve_profile,
)


# ---------------------------------------------------------------------------
# _resolve_profile helpers
# ---------------------------------------------------------------------------


def test_known_opus_model_resolves_to_capable():
    assert _resolve_profile("claude-opus-4-6") == "capable"


def test_known_sonnet_model_resolves_to_capable():
    assert _resolve_profile("claude-sonnet-4-6") == "capable"


def test_known_haiku_model_resolves_to_fast():
    assert _resolve_profile("claude-haiku-4-5-20251001") == "fast"


def test_unknown_model_with_opus_in_name_resolves_to_capable():
    assert _resolve_profile("some-opus-model") == "capable"


def test_unknown_model_defaults_to_capable():
    assert _resolve_profile("totally-unknown-model") == "capable"


# ---------------------------------------------------------------------------
# SwitchBoardClient.complete — happy path
# ---------------------------------------------------------------------------


def _mock_response(content: str, status_code: int = 200):
    """Build a minimal httpx response mock."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }
    mock.raise_for_status = MagicMock()
    mock.text = ""
    return mock


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_returns_assistant_content(mock_post):
    mock_post.return_value = _mock_response("Hello from SwitchBoard")
    client = SwitchBoardClient("http://localhost:20401")
    result = client.complete([{"role": "user", "content": "Hi"}])
    assert result == "Hello from SwitchBoard"


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_sends_to_correct_url(mock_post):
    mock_post.return_value = _mock_response("ok")
    client = SwitchBoardClient("http://switchboard:20401")
    client.complete([{"role": "user", "content": "test"}])
    call_args = mock_post.call_args
    assert call_args[0][0] == "http://switchboard:20401/v1/chat/completions"


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_sets_request_id_header(mock_post):
    mock_post.return_value = _mock_response("ok")
    client = SwitchBoardClient("http://localhost:20401")
    client.complete([{"role": "user", "content": "hi"}], request_id="test-req-001")
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-Request-ID"] == "test-req-001"


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_generates_request_id_when_not_provided(mock_post):
    mock_post.return_value = _mock_response("ok")
    client = SwitchBoardClient("http://localhost:20401")
    client.complete([{"role": "user", "content": "hi"}])
    headers = mock_post.call_args.kwargs["headers"]
    assert "X-Request-ID" in headers
    assert len(headers["X-Request-ID"]) == 32  # UUID hex


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_sets_profile_header_for_opus(mock_post):
    mock_post.return_value = _mock_response("ok")
    client = SwitchBoardClient("http://localhost:20401")
    client.complete([{"role": "user", "content": "hi"}], model="claude-opus-4-6")
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-SwitchBoard-Profile"] == "capable"


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_sets_tenant_id_header(mock_post):
    mock_post.return_value = _mock_response("ok")
    client = SwitchBoardClient("http://localhost:20401", tenant_id="my-service")
    client.complete([{"role": "user", "content": "hi"}])
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-SwitchBoard-Tenant-ID"] == "my-service"


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_includes_model_in_payload(mock_post):
    mock_post.return_value = _mock_response("ok")
    client = SwitchBoardClient("http://localhost:20401")
    client.complete([{"role": "user", "content": "hi"}], model="claude-opus-4-6")
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "claude-opus-4-6"


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_complete_default_tenant_id_is_control_plane(mock_post):
    mock_post.return_value = _mock_response("ok")
    client = SwitchBoardClient("http://localhost:20401")
    client.complete([{"role": "user", "content": "hi"}])
    headers = mock_post.call_args.kwargs["headers"]
    assert headers["X-SwitchBoard-Tenant-ID"] == "control-plane"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_timeout_raises_switchboard_error(mock_post):
    import httpx
    mock_post.side_effect = httpx.TimeoutException("timed out")
    client = SwitchBoardClient("http://localhost:20401")
    with pytest.raises(SwitchBoardError, match="timed out"):
        client.complete([{"role": "user", "content": "hi"}])


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_http_status_error_raises_switchboard_error(mock_post):
    import httpx
    mock_resp = MagicMock()
    mock_resp.status_code = 502
    mock_resp.text = "bad gateway"
    mock_post.return_value = mock_resp
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "502 error", request=MagicMock(), response=mock_resp
    )
    client = SwitchBoardClient("http://localhost:20401")
    with pytest.raises(SwitchBoardError, match="502"):
        client.complete([{"role": "user", "content": "hi"}])


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_connection_error_raises_switchboard_error(mock_post):
    import httpx
    mock_post.side_effect = httpx.ConnectError("connection refused")
    client = SwitchBoardClient("http://localhost:20401")
    with pytest.raises(SwitchBoardError, match="Cannot reach"):
        client.complete([{"role": "user", "content": "hi"}])


@patch("control_plane.adapters.switchboard.client.httpx.post")
def test_malformed_response_raises_switchboard_error(mock_post):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"choices": []}  # empty choices list
    mock_post.return_value = mock
    client = SwitchBoardClient("http://localhost:20401")
    with pytest.raises(SwitchBoardError, match="response shape"):
        client.complete([{"role": "user", "content": "hi"}])
