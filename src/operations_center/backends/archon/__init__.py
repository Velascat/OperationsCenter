# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
backends/archon/ — Optional Archon workflow backend adapter.

Archon is a workflow-oriented premium execution backend. It is optional and
bounded: the rest of the architecture remains independent of Archon internals.

Public API:
    ArchonBackendAdapter — canonical entry point; ExecutionRequest → ExecutionResult

Internal (do not use outside this namespace):
    ArchonWorkflowConfig, ArchonRunCapture, ArchonAdapter, StubArchonAdapter, etc.
"""

from .adapter import ArchonBackendAdapter

__all__ = ["ArchonBackendAdapter"]
