"""
backends/openclaw/ — OpenClaw backend adapter (Phase 11).

OpenClaw as a backend adapter is a backend option behind the canonical contracts.
It is separate from the optional outer-shell integration (openclaw_shell/, Phase 10).

Public API:
    OpenClawBackendAdapter — canonical entry point; ExecutionRequest → ExecutionResult

Internal (do not use outside this namespace):
    OpenClawPreparedRun, OpenClawRunCapture, OpenClawRunner, StubOpenClawRunner, etc.
"""

from .adapter import OpenClawBackendAdapter

__all__ = ["OpenClawBackendAdapter"]
