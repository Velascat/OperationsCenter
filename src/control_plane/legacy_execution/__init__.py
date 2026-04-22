"""Compatibility-only legacy execution runtime.

This package is intentionally quarantined outside the supported ControlPlane
runtime. Supported execution flows must use the canonical planning, policy,
adapter, and observability path instead.
"""

from control_plane.legacy_execution.models import (
    LegacyExecutionRequest,
    LegacyExecutionResult,
)

__all__ = [
    "LegacyExecutionRequest",
    "LegacyExecutionResult",
]
