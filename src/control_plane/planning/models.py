"""
planning/models.py — ControlPlane planning input and output models.

These are ControlPlane-internal types that carry context before a
TaskProposal is built. They do not replace canonical contracts; they
are the raw material the proposal builder consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(frozen=True)
class PlanningContext:
    """Raw context from which a TaskProposal will be shaped.

    ControlPlane derives this from its analysis pipeline (decision rules,
    candidate evaluation, Plane board tasks, etc.). It carries task intent
    without any backend-native execution semantics.
    """

    # What
    goal_text: str
    task_type: str                      # maps to contracts.enums.TaskType value
    execution_mode: str = "goal"        # maps to contracts.enums.ExecutionMode value

    # Where
    repo_key: str = ""
    clone_url: str = ""
    base_branch: str = "main"
    allowed_paths: list[str] = field(default_factory=list)

    # Constraints and risk
    risk_level: str = "low"             # low / medium / high
    priority: str = "normal"            # low / normal / high / critical
    max_changed_files: Optional[int] = None
    timeout_seconds: int = 300

    # Validation
    validation_profile_name: str = "default"
    validation_commands: list[str] = field(default_factory=list)
    require_clean_validation: bool = True

    # Tracing
    task_id: str = ""
    project_id: str = ""
    constraints_text: Optional[str] = None
    labels: list[str] = field(default_factory=list)
    proposer: str = "control-plane"

    # Branch policy
    push_on_success: bool = True
    open_pr: bool = False


@dataclass(frozen=True)
class ProposalBuildResult:
    """Outcome of building a TaskProposal from a PlanningContext."""

    from control_plane.contracts.proposal import TaskProposal

    proposal: "TaskProposal"
    context: PlanningContext
    built_at: datetime = field(default_factory=_utcnow)
    notes: str = ""


@dataclass
class ProposalDecisionBundle:
    """A TaskProposal paired with the LaneDecision that routes it.

    Downstream execution phases use this bundle to construct ExecutionRequest
    without needing to re-derive context or re-query SwitchBoard.
    """

    from control_plane.contracts.proposal import TaskProposal
    from control_plane.contracts.routing import LaneDecision

    proposal: "TaskProposal"
    decision: "LaneDecision"
    context: Optional[PlanningContext] = None
    bundled_at: datetime = field(default_factory=_utcnow)
    trace_notes: str = ""

    @property
    def run_summary(self) -> str:
        return (
            f"proposal={self.proposal.proposal_id[:8]} "
            f"task={self.proposal.task_id} "
            f"lane={self.decision.selected_lane.value} "
            f"backend={self.decision.selected_backend.value} "
            f"rule={self.decision.policy_rule_matched or 'fallback'}"
        )
