# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
execution/handoff.py — canonical execution-request construction.

Supported live handoff:
    TaskProposal + LaneDecision + runtime context -> ExecutionRequest
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from operations_center.contracts.execution import (
    BackendProvenanceMirror,
    BoundExecutionTargetMirror,
    ExecutionRequest,
    RuntimeBindingSummary,
)
from operations_center.lifecycle import LifecycleMetadata
from operations_center.planning.models import ProposalDecisionBundle
from operations_center.policy.models import PolicyDecision


@dataclass(frozen=True)
class ExecutionRuntimeContext:
    """Runtime-resolved details needed to build an ExecutionRequest.

    ``runtime_binding`` is optional and sourced from OC's policy/binder
    layer (not from SwitchBoard's LaneDecision — SB picks lane/backend,
    OC binds the runtime). When set, the coordinator's drift detection
    becomes active for this run.

    ``lifecycle`` (ER-003) is optional. When set, the coordinator wraps
    the dispatch in a plan/execute/verify cycle and attaches the outcome
    to ``ExecutionResult.lifecycle_outcome``.
    """

    workspace_path: Path
    task_branch: str
    goal_file_path: Path | None = None
    runtime_binding: RuntimeBindingSummary | None = None
    lifecycle: LifecycleMetadata | None = None


class ExecutionRequestBuilder:
    """Build canonical ExecutionRequest instances from routed work bundles."""

    def build(
        self,
        bundle: ProposalDecisionBundle,
        runtime: ExecutionRuntimeContext,
        policy_decision: PolicyDecision | None = None,
    ) -> ExecutionRequest:
        proposal = bundle.proposal
        effective_scope = (
            list(policy_decision.effective_scope)
            if policy_decision is not None and policy_decision.effective_scope
            else list(proposal.constraints.allowed_paths or proposal.target.allowed_paths)
        )

        bound_target = _bound_target_from_decision(
            bundle, runtime.runtime_binding,
        )

        return ExecutionRequest(
            proposal_id=proposal.proposal_id,
            decision_id=bundle.decision.decision_id,
            goal_text=proposal.goal_text,
            constraints_text=proposal.constraints_text,
            repo_key=proposal.target.repo_key,
            clone_url=proposal.target.clone_url,
            base_branch=proposal.target.base_branch,
            task_branch=runtime.task_branch,
            workspace_path=Path(runtime.workspace_path),
            goal_file_path=Path(runtime.goal_file_path) if runtime.goal_file_path else None,
            allowed_paths=effective_scope,
            max_changed_files=proposal.constraints.max_changed_files,
            timeout_seconds=proposal.constraints.timeout_seconds,
            require_clean_validation=proposal.constraints.require_clean_validation,
            validation_commands=list(proposal.validation_profile.commands),
            runtime_binding=runtime.runtime_binding,
            bound_target=bound_target,
            lifecycle=runtime.lifecycle,
        )


def _bound_target_from_decision(
    bundle: ProposalDecisionBundle,
    runtime_binding: RuntimeBindingSummary | None,
) -> BoundExecutionTargetMirror | None:
    """Resolve the dispatch-time bound target — including SourceRegistry
    provenance when the registry has an entry for this backend.

    Best-effort: any failure falls back to ``None`` so a missing or
    malformed registry doesn't break dispatch. The validation invariant
    "if backend came from SourceRegistry, source name and SHA are
    visible" is satisfied by populating this when we can; absence is
    correctly None rather than fabricated.
    """
    try:
        from operations_center.execution.binding import _provenance_from_registry
    except ImportError:
        return None

    backend_id = bundle.decision.selected_backend.value
    try:
        provenance = _provenance_from_registry(backend_id)
    except Exception:
        provenance = None

    provenance_mirror = (
        BackendProvenanceMirror(
            source=provenance.source,
            repo=provenance.repo,
            ref=provenance.ref,
            patches=list(provenance.patches),
        )
        if provenance is not None else None
    )

    return BoundExecutionTargetMirror(
        lane=bundle.decision.selected_lane.value,
        backend=backend_id,
        executor=bundle.decision.selected_lane.value,
        runtime_binding=runtime_binding,
        provenance=provenance_mirror,
    )
