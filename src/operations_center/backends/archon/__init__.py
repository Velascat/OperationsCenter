# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
backends/archon/ — Optional Archon workflow backend adapter.

Archon is a workflow-oriented premium execution backend. It is optional and
bounded: the rest of the architecture remains independent of Archon internals.

Public API:
    ArchonBackendAdapter — canonical entry point; ExecutionRequest → ExecutionResult
    HttpArchonAdapter    — concrete archon adapter (health-only today;
                           workflow dispatch is backlogged)
    archon_health_probe  — standalone health probe for ops scripts

Internal (do not use outside this namespace):
    ArchonWorkflowConfig, ArchonRunCapture, ArchonAdapter, StubArchonAdapter, etc.
"""

from .adapter import ArchonBackendAdapter
from .http_client import HealthProbeResult, archon_health_probe
from .invoke import HttpArchonAdapter

__all__ = [
    "ArchonBackendAdapter",
    "HttpArchonAdapter",
    "archon_health_probe",
    "HealthProbeResult",
]
