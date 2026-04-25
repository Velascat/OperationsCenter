"""
execution/handoff.py — canonical execution-request construction.

Supported live handoff:
    TaskProposal + LaneDecision + runtime context -> ExecutionRequest
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from operations_center.contracts.execution import ExecutionRequest
from operations_center.planning.models import ProposalDecisionBundle
from operations_center.policy.models import PolicyDecision


@dataclass(frozen=True)
class ExecutionRuntimeContext:
    """Runtime-resolved details needed to build an ExecutionRequest."""

    workspace_path: Path
    task_branch: str
    goal_file_path: Path | None = None


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
        )
