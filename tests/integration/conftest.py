"""Shared fixtures for integration tests that require live services."""

from __future__ import annotations

import os

import httpx
import pytest


def _check_switchboard(url: str) -> bool:
    try:
        httpx.get(f"{url}/health", timeout=3.0)
        return True
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


@pytest.fixture(scope="session")
def switchboard_url() -> str:
    """Return the SwitchBoard base URL, skipping if the service is not reachable."""
    url = os.environ.get("CONTROL_PLANE_SWITCHBOARD_URL", "http://localhost:20401")
    if not _check_switchboard(url):
        pytest.skip(
            f"SwitchBoard not reachable at {url}. "
            f"Start the stack first or set CONTROL_PLANE_SWITCHBOARD_URL."
        )
    return url
