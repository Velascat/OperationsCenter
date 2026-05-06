# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Bidirectional mappers between OperationsCenter's internal Pydantic
contracts and the canonical CxRP v0.2 envelope.

OC's internal types (Pydantic) carry richer constraints than CxRP's wire
envelope: typed enums, structured ``TaskTarget``/``BranchPolicy``/
``ValidationProfile`` objects, etc. CxRP defines the *envelope* — abstract
``lane`` category plus open-string ``executor``/``backend`` and free-form
``input_payload``. These mappers translate at the wire boundary so that
inter-repo communication uses CxRP shape.

Mapping conventions:

* OC ``selected_lane`` (e.g. ``claude_cli``) → CxRP ``executor``
* OC ``selected_backend`` (e.g. ``kodo``)   → CxRP ``backend``
* CxRP abstract ``lane`` is derived from the OC lane name.
* OC's rich ``ExecutionArtifact`` (id, label, content, size) collapses
  to CxRP ``Artifact`` (kind, uri, description, metadata) — id/label/size
  travel in ``metadata``.
* OC's ``ValidationSummary``/``ChangedFileRef`` lists travel in
  ``ExecutionResult.diagnostics``.

Inverse helpers (``from_cxrp_*``) are intentionally minimal — only the
fields downstream OC needs at the consume boundary are reconstructed.
"""

from __future__ import annotations

from typing import Any

from cxrp.contracts import (
    Artifact as CxrpArtifact,
    ExecutionLimits as CxrpExecutionLimits,
    ExecutionRequest as CxrpExecutionRequest,
    ExecutionResult as CxrpExecutionResult,
    LaneAlternative as CxrpLaneAlternative,
    LaneDecision as CxrpLaneDecision,
    RuntimeBinding as CxrpRuntimeBinding,
    TaskProposal as CxrpTaskProposal,
)
from cxrp.vocabulary.lane import LaneType
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode
from cxrp.vocabulary.status import ExecutionStatus as CxrpExecutionStatus

from .enums import BackendName, LaneName
from .execution import ExecutionRequest, ExecutionResult, RuntimeBindingSummary
from .proposal import TaskProposal
from .routing import LaneDecision

CODING_AGENT_INPUT_SCHEMA_ID = "coding_agent_input/v0.2"
CODING_AGENT_TARGET_SCHEMA_ID = "coding_agent_target/v0.2"

_OC_LANE_TO_ECP_CATEGORY: dict[str, LaneType] = {
    "claude_cli": LaneType.CODING_AGENT,
    "codex_cli": LaneType.CODING_AGENT,
    "aider_local": LaneType.CODING_AGENT,
}


def _category_for(oc_lane_value: str) -> LaneType:
    return _OC_LANE_TO_ECP_CATEGORY.get(oc_lane_value, LaneType.CODING_AGENT)


def to_cxrp_task_proposal(oc: TaskProposal) -> CxrpTaskProposal:
    """Translate an OC TaskProposal into the CxRP v0.2 envelope."""
    target_payload: dict[str, Any] = {
        "$payload_schema": CODING_AGENT_TARGET_SCHEMA_ID,
        "repo_key": oc.target.repo_key,
        "clone_url": oc.target.clone_url,
        "base_branch": oc.target.base_branch,
    }
    return CxrpTaskProposal(
        proposal_id=oc.proposal_id,
        created_at=oc.proposed_at,
        metadata={
            "task_id": oc.task_id,
            "project_id": oc.project_id,
            "proposer": oc.proposer,
            "labels": list(oc.labels),
        },
        title=oc.goal_text[:80],
        objective=oc.goal_text,
        task_type=oc.task_type.value,
        execution_mode=oc.execution_mode.value,
        priority=oc.priority.value,
        risk_level=oc.risk_level.value,
        target=target_payload,
        constraints=[oc.constraints_text] if oc.constraints_text else [],
    )


def to_cxrp_lane_decision(
    oc: LaneDecision, *, extra_metadata: dict[str, Any] | None = None
) -> CxrpLaneDecision:
    """Translate an OC LaneDecision into CxRP envelope shape.

    Mirrors switchboard.adapters.cxrp_mapper but lives here so OC's own
    audit/observability code can emit the same wire shape.
    """
    metadata: dict[str, Any] = {
        "policy_rule_matched": oc.policy_rule_matched,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    return CxrpLaneDecision(
        decision_id=oc.decision_id,
        proposal_id=oc.proposal_id,
        created_at=oc.decided_at,
        metadata=metadata,
        lane=_category_for(oc.selected_lane.value),
        executor=oc.selected_lane.value,
        backend=oc.selected_backend.value,
        rationale=oc.rationale or "",
        confidence=oc.confidence,
        alternatives=[
            CxrpLaneAlternative(lane=_category_for(alt.value), executor=alt.value)
            for alt in oc.alternatives_considered
        ],
    )


def from_cxrp_lane_decision(payload: dict[str, Any]) -> LaneDecision:
    """Reconstruct an OC ``LaneDecision`` from an CxRP wire payload.

    Used at the OC consume boundary (``HttpLaneRoutingClient``) to accept
    SwitchBoard's CxRP-shaped ``/route`` response. Narrows CxRP's
    open-string ``executor``/``backend`` back into OC's ``LaneName``/
    ``BackendName`` ``Literal`` constraints; raises ``ValueError`` if the
    incoming executor or backend is not one OC recognises.
    """
    executor = payload.get("executor")
    backend = payload.get("backend")
    if executor is None or backend is None:
        raise ValueError(
            "CxRP LaneDecision missing executor/backend; "
            "OC requires both to narrow into LaneName/BackendName."
        )
    metadata = payload.get("metadata") or {}
    return LaneDecision(
        decision_id=payload["decision_id"],
        proposal_id=payload["proposal_id"],
        selected_lane=LaneName(executor),
        selected_backend=BackendName(backend),
        confidence=payload.get("confidence", 1.0),
        policy_rule_matched=metadata.get("policy_rule_matched"),
        rationale=payload.get("rationale") or None,
        alternatives_considered=[
            LaneName(alt["executor"])
            for alt in payload.get("alternatives", [])
            if alt.get("executor") is not None
        ],
    )


def to_cxrp_execution_request(oc: ExecutionRequest, *, executor: str, backend: str) -> CxrpExecutionRequest:
    """Translate an OC ExecutionRequest into CxRP shape.

    OC's request is rich (workspace paths, branches, validation commands).
    Those fields land in CxRP's ``input_payload`` under the
    ``coding_agent_input/v0.2`` payload schema. Boundary-universal caps
    (file count, timeout) become CxRP ``limits``.
    """
    input_payload: dict[str, Any] = {
        "goal_text": oc.goal_text,
        "constraints_text": oc.constraints_text,
        "repo_key": oc.repo_key,
        "clone_url": oc.clone_url,
        "base_branch": oc.base_branch,
        "task_branch": oc.task_branch,
        "workspace_path": str(oc.workspace_path),
        "goal_file_path": str(oc.goal_file_path) if oc.goal_file_path else None,
        "allowed_paths": list(oc.allowed_paths),
        "validation_commands": list(oc.validation_commands),
    }
    runtime_binding_cxrp = (
        runtime_binding_from_summary(oc.runtime_binding)
        if oc.runtime_binding is not None
        else None
    )
    # Schema 0.3 — backend/executor are typed CxRP enums
    from cxrp.contracts import BackendName as CxrpBackendName, ExecutorName as CxrpExecutorName
    return CxrpExecutionRequest(
        request_id=oc.run_id,
        proposal_id=oc.proposal_id,
        lane_decision_id=oc.decision_id,
        created_at=oc.requested_at,
        metadata={"executor": executor, "backend": backend},
        lane=_category_for(executor),
        executor=CxrpExecutorName(executor),
        backend=CxrpBackendName(backend),
        scope=oc.goal_text[:120],
        input_payload=input_payload,
        input_payload_schema=CODING_AGENT_INPUT_SCHEMA_ID,
        constraints=[oc.constraints_text] if oc.constraints_text else [],
        limits=CxrpExecutionLimits(
            max_changed_files=oc.max_changed_files,
            timeout_seconds=oc.timeout_seconds,
            require_clean_validation=oc.require_clean_validation,
        ),
        runtime_binding=runtime_binding_cxrp,
    )


def _ecp_status_for(oc_status_value: str) -> CxrpExecutionStatus:
    return CxrpExecutionStatus(oc_status_value)


def to_cxrp_execution_result(oc: ExecutionResult) -> CxrpExecutionResult:
    """Translate an OC ExecutionResult into CxRP shape.

    OC's rich ``ExecutionArtifact`` collapses to CxRP ``Artifact``. Heavy
    sub-objects (validation summary, changed-file refs, telemetry) move
    into ``diagnostics`` so the envelope stays small.
    """
    artifacts: list[CxrpArtifact] = [
        CxrpArtifact(
            kind=art.artifact_type.value,
            uri=art.uri or "",
            description=art.label,
            metadata={
                "artifact_id": art.artifact_id,
                "size_bytes": art.size_bytes,
                "produced_at": art.produced_at.isoformat(),
            },
        )
        for art in oc.artifacts
    ]
    diagnostics: dict[str, Any] = {
        "validation_status": oc.validation.status.value,
        "branch_pushed": oc.branch_pushed,
        "branch_name": oc.branch_name,
        "pull_request_url": oc.pull_request_url,
        "failure_category": oc.failure_category.value if oc.failure_category else None,
        "failure_reason": oc.failure_reason,
        "changed_files_count": len(oc.changed_files),
    }
    return CxrpExecutionResult(
        result_id=oc.run_id,
        created_at=oc.completed_at,
        metadata={"proposal_id": oc.proposal_id, "decision_id": oc.decision_id},
        request_id=oc.run_id,
        ok=oc.success,
        status=_ecp_status_for(oc.status.value),
        artifacts=artifacts,
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# RuntimeBinding mapping (CxRP <-> OC summary)
# ---------------------------------------------------------------------------

def runtime_binding_to_summary(rb: CxrpRuntimeBinding) -> RuntimeBindingSummary:
    """Translate a CxRP RuntimeBinding (validated dataclass) into the
    OC contract-layer summary that rides on ExecutionRequest.runtime_binding.
    """
    return RuntimeBindingSummary(
        kind=rb.kind.value if isinstance(rb.kind, RuntimeKind) else str(rb.kind),
        selection_mode=(
            rb.selection_mode.value
            if isinstance(rb.selection_mode, SelectionMode)
            else str(rb.selection_mode)
        ),
        model=rb.model,
        provider=rb.provider,
        endpoint=rb.endpoint,
        config_ref=rb.config_ref,
    )


def runtime_binding_from_summary(summary: RuntimeBindingSummary) -> CxrpRuntimeBinding:
    """Inverse of ``runtime_binding_to_summary``. Re-validates against
    CxRP's validity table + optional-field allow-list — raises ValueError
    if the summary was constructed with an inconsistent shape.
    """
    return CxrpRuntimeBinding(
        kind=RuntimeKind(summary.kind),
        selection_mode=SelectionMode(summary.selection_mode),
        model=summary.model,
        provider=summary.provider,
        endpoint=summary.endpoint,
        config_ref=summary.config_ref,
    )
