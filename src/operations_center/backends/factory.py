# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/factory.py — canonical backend adapter registry.

The registry resolves a routed backend name to a canonical adapter that accepts
ExecutionRequest and returns ExecutionResult.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Mapping, Protocol

if TYPE_CHECKING:
    from operations_center.backends.archon.invoke import ArchonAdapter
    from operations_center.backends.openclaw.invoke import OpenClawRunner

from operations_center.config.settings import Settings
from operations_center.contracts.enums import BackendName
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

from .aider_local import AiderLocalBackendAdapter
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
        archon_adapter: "ArchonAdapter | None" = None,
        openclaw_runner: "OpenClawRunner | None" = None,
    ) -> "CanonicalBackendRegistry":
        adapters: dict[BackendName, CanonicalBackendAdapter] = {
            BackendName.KODO: KodoBackendAdapter.from_settings(
                settings=settings.kodo,
            ),
            BackendName.DIRECT_LOCAL: DirectLocalBackendAdapter(
                settings.aider,
            ),
            BackendName.AIDER_LOCAL: AiderLocalBackendAdapter(
                settings.aider_local,
            ),
        }
        if archon_adapter is not None:
            archon_backend = ArchonBackendAdapter(
                archon_adapter=archon_adapter,
            )
            adapters[BackendName.ARCHON] = archon_backend
            adapters[BackendName.ARCHON_THEN_KODO] = archon_backend
        if openclaw_runner is not None:
            adapters[BackendName.OPENCLAW] = OpenClawBackendAdapter(
                runner=openclaw_runner,
            )
        return cls(adapters)
