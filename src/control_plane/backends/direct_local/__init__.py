"""
backends/direct_local — local execution backend adapter.

This adapter backs the `aider_local/direct_local` routing target behind the
canonical execution contracts.
"""

from .adapter import DirectLocalBackendAdapter

__all__ = ["DirectLocalBackendAdapter"]
