# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Drift detection engine — comparison logic + BACKEND_DRIFT findings.

Adapters report what happened; OperationsCenter (this module) decides
whether it is drift.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class DriftKind(str, Enum):
    RUNTIME          = "runtime"
    CAPABILITY       = "capability"
    OUTPUT_SHAPE     = "output_shape"
    INTERNAL_ROUTING = "internal_routing"


@dataclass(frozen=True)
class BackendDriftFinding:
    """The single canonical BACKEND_DRIFT finding shape.

    Matches the schema in docs/architecture/backend_control_audit.md
    (System Phase — Drift Detection).
    """

    backend_id: str
    request_id: str
    drift_type: DriftKind
    observed: dict[str, Any]
    bound_or_allowed: dict[str, Any]
    impact: str

    rule: str = "BACKEND_DRIFT"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["drift_type"] = self.drift_type.value
        return d


# Per-result schema reference for output_shape drift detection.
_CXRP_RESULT_TOP_LEVEL_FIELDS: frozenset[str] = frozenset({
    "schema_version", "contract_kind", "result_id", "request_id", "ok",
    "status", "summary", "artifacts", "diagnostics", "evidence",
    "metadata", "created_at",
})


def detect_runtime_drift(
    *,
    backend_id: str,
    request_id: str,
    bound_runtime: dict[str, Any],
    observed_runtime: dict[str, Any],
) -> BackendDriftFinding | None:
    """Compare bound vs observed runtime. Drift = any key in bound that
    differs in observed (with non-None observed value)."""
    diffs = {
        k: {"bound": v, "observed": observed_runtime.get(k)}
        for k, v in bound_runtime.items()
        if observed_runtime.get(k) is not None and observed_runtime.get(k) != v
    }
    if not diffs:
        return None
    return BackendDriftFinding(
        backend_id=backend_id,
        request_id=request_id,
        drift_type=DriftKind.RUNTIME,
        observed=observed_runtime,
        bound_or_allowed=bound_runtime,
        impact=f"backend executed with runtime fields differing from bound: {sorted(diffs)}",
    )


def detect_capability_drift(
    *,
    backend_id: str,
    request_id: str,
    allowed_capabilities: set[str],
    used_capabilities: set[str],
) -> BackendDriftFinding | None:
    """Drift = backend used any capability outside the allowed set."""
    over = used_capabilities - allowed_capabilities
    if not over:
        return None
    return BackendDriftFinding(
        backend_id=backend_id,
        request_id=request_id,
        drift_type=DriftKind.CAPABILITY,
        observed={"used": sorted(used_capabilities)},
        bound_or_allowed={"allowed": sorted(allowed_capabilities)},
        impact=f"backend used unauthorized capabilities: {sorted(over)}",
    )


def detect_output_shape_drift(
    *,
    backend_id: str,
    request_id: str,
    result_payload: dict[str, Any],
) -> BackendDriftFinding | None:
    """Drift = result has top-level field outside the CxRP schema.

    This is the second-line check — the JSON schema's
    ``additionalProperties: false`` will already reject this at the
    validation gate, but the drift finding gives queryable provenance
    of which adapter produced the bad shape.
    """
    extra = set(result_payload.keys()) - _CXRP_RESULT_TOP_LEVEL_FIELDS
    if not extra:
        return None
    return BackendDriftFinding(
        backend_id=backend_id,
        request_id=request_id,
        drift_type=DriftKind.OUTPUT_SHAPE,
        observed={"extra_top_level_fields": sorted(extra)},
        bound_or_allowed={"schema": "CxRP ExecutionResult v0.2"},
        impact=f"backend returned top-level fields outside CxRP schema: {sorted(extra)}",
    )


def detect_internal_routing_drift(
    *,
    backend_id: str,
    request_id: str,
    bound_agent_models: dict[str, str],
    observed_agent_models: dict[str, str],
) -> BackendDriftFinding | None:
    """Drift = any internal agent ran a different model than OC pinned for it."""
    diffs = {
        agent: {"bound": bound_agent_models[agent], "observed": observed_agent_models.get(agent)}
        for agent in bound_agent_models
        if observed_agent_models.get(agent) is not None
        and observed_agent_models.get(agent) != bound_agent_models[agent]
    }
    if not diffs:
        return None
    return BackendDriftFinding(
        backend_id=backend_id,
        request_id=request_id,
        drift_type=DriftKind.INTERNAL_ROUTING,
        observed=observed_agent_models,
        bound_or_allowed=bound_agent_models,
        impact=f"internal agents diverged from pinned models: {sorted(diffs)}",
    )
