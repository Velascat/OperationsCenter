# src/control_plane/adapters/switchboard/client.py
"""Thin synchronous HTTP client for SwitchBoard's OpenAI-compatible chat completions API.

Usage:
    client = SwitchBoardClient("http://localhost:20401")
    text = client.complete(
        messages=[{"role": "user", "content": "Hello"}],
        model="capable",
        intent="brainstorm",
    )
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx

# Claude model names that ControlPlane uses → SwitchBoard profile
_MODEL_TO_PROFILE: dict[str, str] = {
    "claude-opus-4-6": "capable",
    "claude-opus-4-7": "capable",
    "claude-sonnet-4-6": "capable",
    "claude-haiku-4-5-20251001": "fast",
}


def _resolve_profile(model: str) -> str:
    if model in _MODEL_TO_PROFILE:
        return _MODEL_TO_PROFILE[model]
    if "opus" in model or "sonnet" in model:
        return "capable"
    if "haiku" in model:
        return "fast"
    return "capable"


class SwitchBoardError(Exception):
    """Raised when SwitchBoard returns an error or is unreachable."""


class SwitchBoardClient:
    """Sends chat completion requests to SwitchBoard and returns the assistant text.

    Args:
        base_url:   SwitchBoard base URL, e.g. ``"http://localhost:20401"``.
        timeout:    Per-request timeout in seconds.
        tenant_id:  Value for ``X-SwitchBoard-Tenant-ID`` — used to identify
                    Control Plane traffic in the SwitchBoard decision log.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        tenant_id: str = "control-plane",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._tenant_id = tenant_id

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "capable",
        request_id: str | None = None,
    ) -> str:
        """Send ``messages`` to SwitchBoard and return the assistant reply text.

        Args:
            messages:    OpenAI-format message list.
            model:       Model hint — either a SwitchBoard profile name
                         (``"capable"``, ``"fast"``) or a Claude model identifier
                         (e.g. ``"claude-sonnet-4-6"``).  SwitchBoard policy
                         may override this.
            request_id:  Correlation ID for this request.  Auto-generated as a
                         UUID hex if not supplied.  Visible in
                         ``GET /admin/decisions/{request_id}``.

        Returns:
            The assistant message content string.

        Raises:
            SwitchBoardError: If the HTTP call fails or the response is malformed.
        """
        rid = request_id or uuid.uuid4().hex
        profile = _resolve_profile(model)

        headers: dict[str, str] = {
            "X-Request-ID": rid,
            "X-SwitchBoard-Tenant-ID": self._tenant_id,
            "X-SwitchBoard-Profile": profile,
        }

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }

        try:
            resp = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SwitchBoardError(f"SwitchBoard request timed out: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise SwitchBoardError(
                f"SwitchBoard returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:200]}"
            ) from exc
        except httpx.RequestError as exc:
            raise SwitchBoardError(f"Cannot reach SwitchBoard at {self._base_url}: {exc}") from exc

        try:
            data = resp.json()
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as exc:
            raise SwitchBoardError(f"Unexpected SwitchBoard response shape: {exc}") from exc
