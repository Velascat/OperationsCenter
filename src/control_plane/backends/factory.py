"""
backends/factory.py — canonical backend adapter registry.

The registry resolves a routed backend name to a canonical adapter that accepts
ExecutionRequest and returns ExecutionResult.
"""

from __future__ import annotations

import os
from typing import Mapping, Protocol

from control_plane.config.settings import Settings
from control_plane.contracts.enums import BackendName
from control_plane.contracts.execution import ExecutionRequest, ExecutionResult

from .archon import ArchonBackendAdapter
from .direct_local import DirectLocalBackendAdapter
from .kodo import KodoBackendAdapter
from .openclaw import OpenClawBackendAdapter


class CanonicalBackendAdapter(Protocol):
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        ...


class UnsupportedBackendError(RuntimeError):
    """Raised when the runtime has no configured canonical adapter for a backend."""


class CanonicalBackendRegistry:
    """Maps canonical backend names to canonical adapters."""

    def __init__(self, adapters: Mapping[BackendName, CanonicalBackendAdapter]) -> None:
        self._adapters = dict(adapters)

    def for_backend(self, backend: BackendName) -> CanonicalBackendAdapter:
        adapter = self._adapters.get(backend)
        if adapter is None:
            raise UnsupportedBackendError(
                f"No canonical adapter configured for backend '{backend.value}'."
            )
        return adapter

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        archon_adapter=None,
        openclaw_runner=None,
    ) -> "CanonicalBackendRegistry":
        switchboard_url = _resolve_switchboard_url(settings)
        adapters: dict[BackendName, CanonicalBackendAdapter] = {
            BackendName.KODO: KodoBackendAdapter.from_settings(
                settings=settings.kodo,
                switchboard_url=switchboard_url,
            ),
            BackendName.DIRECT_LOCAL: DirectLocalBackendAdapter(
                settings.aider,
                switchboard_url=switchboard_url,
            ),
        }
        if archon_adapter is not None:
            archon_backend = ArchonBackendAdapter(
                archon_adapter=archon_adapter,
                switchboard_url=switchboard_url,
            )
            adapters[BackendName.ARCHON] = archon_backend
            adapters[BackendName.ARCHON_THEN_KODO] = archon_backend
        if openclaw_runner is not None:
            adapters[BackendName.OPENCLAW] = OpenClawBackendAdapter(
                runner=openclaw_runner,
                switchboard_url=switchboard_url,
            )
        return cls(adapters)


def _resolve_switchboard_url(settings: Settings) -> str:
    from_env = os.environ.get("SWITCHBOARD_URL", "")
    if from_env:
        return from_env
    configured = getattr(settings.spec_director, "switchboard_url", None)
    return configured or ""
