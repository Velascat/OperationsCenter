from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

import httpx
import pytest

from control_plane.adapters.escalation import post_escalation


def test_post_escalation_sends_correct_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy-path: valid webhook_url produces the expected JSON POST."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    # Patch httpx.Client so post_escalation uses our mock transport.
    _original_client = httpx.Client

    class _MockClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _MockClient)  # type: ignore[attr-defined]

    now = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
    post_escalation(
        "https://hooks.example.com/escalate",
        classification="critical",
        count=5,
        task_ids=["T-1", "T-2"],
        now=now,
    )

    assert len(captured) == 1
    req = captured[0]
    assert req.method == "POST"
    assert str(req.url) == "https://hooks.example.com/escalate"

    body = json.loads(req.content.decode())
    assert body == {
        "event": "escalation_threshold_reached",
        "classification": "critical",
        "count": 5,
        "task_ids": ["T-1", "T-2"],
        "timestamp": "2026-04-06T12:00:00+00:00",
    }


def test_post_escalation_client_uses_timeout_10(monkeypatch: pytest.MonkeyPatch) -> None:
    """The httpx.Client must be created with timeout=10."""
    init_kwargs: list[dict[str, object]] = []

    _original_client = httpx.Client

    class _SpyClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            init_kwargs.append(dict(kwargs))
            kwargs["transport"] = httpx.MockTransport(
                lambda _: httpx.Response(200, json={"ok": True})
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _SpyClient)  # type: ignore[attr-defined]

    now = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
    post_escalation(
        "https://hooks.example.com/escalate",
        classification="warn",
        count=3,
        task_ids=["T-3"],
        now=now,
    )

    assert len(init_kwargs) == 1
    assert init_kwargs[0]["timeout"] == 10


def test_post_escalation_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """post_escalation should always return None."""
    _original_client = httpx.Client

    class _StubClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(
                lambda _: httpx.Response(200, json={"ok": True})
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _StubClient)  # type: ignore[attr-defined]

    result = post_escalation(
        "https://hooks.example.com/escalate",
        classification="info",
        count=1,
        task_ids=["T-4"],
        now=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert result is None


# ---------------------------------------------------------------------------
# Guard-clause tests: empty / None webhook_url → early return, no HTTP call
# ---------------------------------------------------------------------------


def test_post_escalation_empty_url_skips_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty-string webhook_url must cause an early return with no HTTP call."""
    called = False

    _original_client = httpx.Client

    class _FailClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            nonlocal called
            called = True
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _FailClient)  # type: ignore[attr-defined]

    result = post_escalation(
        "",
        classification="critical",
        count=1,
        task_ids=["T-5"],
        now=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert result is None
    assert not called, "httpx.Client should not be instantiated for empty webhook_url"


def test_post_escalation_none_url_skips_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """A None webhook_url must also trigger the early-return guard."""
    called = False

    _original_client = httpx.Client

    class _FailClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            nonlocal called
            called = True
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _FailClient)  # type: ignore[attr-defined]

    result = post_escalation(
        cast(str, None),
        classification="critical",
        count=2,
        task_ids=["T-6"],
        now=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert result is None
    assert not called, "httpx.Client should not be instantiated for None webhook_url"


# ---------------------------------------------------------------------------
# Error-swallowing tests: exceptions from HTTP layer must not propagate
# ---------------------------------------------------------------------------


def test_post_escalation_swallows_connect_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx.ConnectError must be silently swallowed."""
    _original_client = httpx.Client

    class _ErrorClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(
                lambda req: (_ for _ in ()).throw(
                    httpx.ConnectError("connection refused")
                )
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _ErrorClient)  # type: ignore[attr-defined]

    # Must not raise
    result = post_escalation(
        "https://hooks.example.com/escalate",
        classification="critical",
        count=3,
        task_ids=["T-7"],
        now=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert result is None


def test_post_escalation_swallows_timeout_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """httpx.TimeoutException must be silently swallowed."""
    _original_client = httpx.Client

    class _TimeoutClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(
                lambda req: (_ for _ in ()).throw(
                    httpx.TimeoutException("read timed out")
                )
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _TimeoutClient)  # type: ignore[attr-defined]

    result = post_escalation(
        "https://hooks.example.com/escalate",
        classification="warn",
        count=4,
        task_ids=["T-8"],
        now=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert result is None


def test_post_escalation_swallows_arbitrary_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any arbitrary exception from the HTTP layer must be swallowed."""
    _original_client = httpx.Client

    class _BoomClient(_original_client):  # type: ignore[misc]
        def __init__(self, **kwargs: Any) -> None:
            kwargs["transport"] = httpx.MockTransport(
                lambda req: (_ for _ in ()).throw(
                    RuntimeError("unexpected kaboom")
                )
            )
            super().__init__(**kwargs)

    monkeypatch.setattr(httpx, "Client", _BoomClient)  # type: ignore[attr-defined]

    result = post_escalation(
        "https://hooks.example.com/escalate",
        classification="info",
        count=5,
        task_ids=["T-9"],
        now=datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc),
    )
    assert result is None
