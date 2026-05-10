# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""BoundExecutionTarget — OC's strict, validated execution target.

Distinct from CxRP's ``ExecutionTargetEnvelope``: this is the
post-validation, post-binding shape that the coordinator hands to
adapters. Unknown executors/backends from CxRP are rejected at the
binding step and never reach this type.

See docs/architecture/contracts/execution_target.md (this repo) and
CxRP docs/spec/execution_target.md for the asymmetry rationale.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.execution import RuntimeBindingSummary


class BackendProvenance(BaseModel):
    """OC-owned source-of-truth for which fork/ref/patches power a backend.

    Filled from the SourceRegistry (``registry/source_registry.yaml`` +
    ``registry/patches/``), not from CxRP. Two OC deployments routing to
    the same backend may have different provenance; that's a runtime
    fact OC owns.
    """

    source: Literal["registry", "local", "upstream", "unknown"] = "unknown"
    repo: Optional[str] = None
    ref: Optional[str] = None
    patches: list[str] = Field(default_factory=list)

    model_config = {"frozen": True}


class BoundExecutionTarget(BaseModel):
    """The strict, validated, dispatch-ready execution target.

    Produced by ``bind_execution_target(envelope, catalog, policy)``.
    Adapters consume this. ExecutionResult records it for replay/audit.

    Field semantics:
      - ``lane`` — CxRP abstract work category (``coding_agent``,
        ``review_agent``, etc.). Open string; OC doesn't currently
        enumerate categories.
      - ``executor`` — OC ``LaneName`` enum (``claude_cli`` / ``codex_cli`` /
        ``aider_local``). Strict.
      - ``backend`` — OC ``BackendName`` enum (``kodo`` / ``archon`` / ...).
        Strict.
    """

    lane: str
    backend: BackendName
    executor: Optional[LaneName] = None
    runtime_binding: Optional[RuntimeBindingSummary] = None
    provenance: Optional[BackendProvenance] = None

    model_config = {"frozen": True}
