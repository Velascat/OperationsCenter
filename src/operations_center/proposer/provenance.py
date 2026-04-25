from __future__ import annotations

from pydantic import BaseModel, Field

from operations_center.decision.models import ProposalCandidate, ProposalCandidatesArtifact
from operations_center.insights.models import RepoInsightsArtifact


class ProposalProvenance(BaseModel):
    source: str = "autonomy-proposer"
    repo_name: str
    source_family: str
    candidate_id: str
    candidate_dedup_key: str
    observer_run_ids: list[str] = Field(default_factory=list)
    insight_run_id: str
    decision_run_id: str
    proposer_run_id: str


def build_provenance(
    *,
    candidate: ProposalCandidate,
    decision_artifact: ProposalCandidatesArtifact,
    insight_artifact: RepoInsightsArtifact,
    proposer_run_id: str,
) -> ProposalProvenance:
    return ProposalProvenance(
        repo_name=decision_artifact.repo.name,
        source_family=candidate.family,
        candidate_id=candidate.candidate_id,
        candidate_dedup_key=candidate.dedup_key,
        observer_run_ids=[snapshot.run_id for snapshot in insight_artifact.source_snapshots],
        insight_run_id=insight_artifact.run_id,
        decision_run_id=decision_artifact.run_id,
        proposer_run_id=proposer_run_id,
    )
