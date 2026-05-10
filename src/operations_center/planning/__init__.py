# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
planning/ — Context shaping and proposal construction.

Public API:
    PlanningContext      — raw context from which a TaskProposal is built
    ProposalBuildResult  — proposal + context, returned from build_proposal_with_result
    ProposalDecisionBundle — proposal + LaneDecision, ready for execution
    build_proposal       — PlanningContext → TaskProposal
    build_proposal_with_result — PlanningContext → ProposalBuildResult
"""

from .models import PlanningContext, ProposalBuildResult, ProposalDecisionBundle
from .proposal_builder import build_proposal, build_proposal_with_result

__all__ = [
    "PlanningContext",
    "ProposalBuildResult",
    "ProposalDecisionBundle",
    "build_proposal",
    "build_proposal_with_result",
]
