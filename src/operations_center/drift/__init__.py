# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Drift detection layer — system-wide, not per-backend.

Backend drift is when an executor's observed behavior differs from what
OC bound or allowed. Four kinds:

  - runtime          — backend used a different runtime than bound
  - capability       — backend used a capability outside policy
  - output_shape     — backend returned a top-level field outside the
                       CxRP ExecutionResult schema
  - internal_routing — multi-agent backend's internal orchestration
                       bypassed OC constraints

See OperationsCenter/docs/architecture/audit/backend_control_audit.md
(System Phase — Drift Detection).
"""
from operations_center.drift.engine import (
    BackendDriftFinding,
    DriftKind,
    detect_capability_drift,
    detect_output_shape_drift,
    detect_runtime_drift,
)

__all__ = [
    "BackendDriftFinding",
    "DriftKind",
    "detect_capability_drift",
    "detect_output_shape_drift",
    "detect_runtime_drift",
]
