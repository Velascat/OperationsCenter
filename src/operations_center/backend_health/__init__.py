# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Runtime backend health model and registry."""

from .models import (
    BackendFailure,
    BackendHealthRecord,
    BackendHealthState,
    RecoveryStrategy,
)
from .registry import BackendHealthRegistry, HealthTransition

__all__ = [
    "BackendFailure",
    "BackendHealthRecord",
    "BackendHealthRegistry",
    "BackendHealthState",
    "HealthTransition",
    "RecoveryStrategy",
]
