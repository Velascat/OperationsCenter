# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""HTTP client helpers for talking to a running Archon instance.

Today this module only does a health probe. Real workflow dispatch
(POST conversation → run workflow → poll/stream results → map status)
is deferred — see ``.console/log.md`` *"Archon real workflow integration"*.

Archon is deployed by WorkStation
(``compose/profiles/archon.yml`` in the WorkStation repo) at
``http://localhost:3000`` by default.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx


DEFAULT_BASE_URL = "http://localhost:3000"
HEALTH_PATH = "/api/health"


@dataclass(frozen=True)
class HealthProbeResult:
    """Outcome of a single health probe against an Archon instance."""

    ok: bool
    base_url: str
    status_code: int | None
    summary: str

    @property
    def reachable(self) -> bool:
        return self.status_code is not None


def archon_health_probe(
    base_url: str = DEFAULT_BASE_URL,
    *,
    timeout_seconds: float = 5.0,
    client: httpx.Client | None = None,
) -> HealthProbeResult:
    """Probe Archon's ``/api/health`` endpoint.

    Returns a structured result rather than raising — callers
    (the concrete adapter, ops scripts, monitors) decide what to do.
    """
    url = base_url.rstrip("/") + HEALTH_PATH
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.get(url)
        except httpx.TimeoutException:
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary=f"timeout after {timeout_seconds}s probing {url}",
            )
        except httpx.ConnectError as exc:
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary=f"connect error: {exc}",
            )
        except Exception as exc:  # any other transport-level failure
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary=f"http error: {exc}",
            )
    finally:
        if owns_client:
            client.close()

    ok = 200 <= response.status_code < 300
    summary = (
        "archon healthy" if ok
        else f"archon unhealthy (HTTP {response.status_code})"
    )
    return HealthProbeResult(
        ok=ok, base_url=base_url, status_code=response.status_code,
        summary=summary,
    )
