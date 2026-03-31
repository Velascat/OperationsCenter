from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from control_plane.decision.artifact_writer import DecisionArtifactWriter
from control_plane.decision.models import DecisionRepoRef, ProposalCandidatesArtifact
from control_plane.decision.policy import DecisionPolicy, DecisionPolicyConfig
from control_plane.decision.rules.dependency_drift import DependencyDriftRule
from control_plane.decision.rules.hotspot_concentration import HotspotConcentrationRule
from control_plane.decision.rules.observation_coverage import ObservationCoverageRule
from control_plane.decision.rules.test_visibility import TestVisibilityRule
from control_plane.decision.rules.todo_accumulation import TodoAccumulationRule


class DecisionLoaderProtocol(Protocol):
    def load(self, *, repo: str | None, insight_run_id: str | None, history_limit: int):
        ...


@dataclass(frozen=True)
class DecisionContext:
    repo_filter: str | None
    insight_run_id: str | None
    history_limit: int
    max_candidates: int
    cooldown_minutes: int
    run_id: str
    generated_at: datetime
    source_command: str


class DecisionEngineService:
    def __init__(
        self,
        *,
        loader: DecisionLoaderProtocol,
        policy: DecisionPolicy | None = None,
        artifact_writer: DecisionArtifactWriter | None = None,
    ) -> None:
        self.loader = loader
        self.policy = policy or DecisionPolicy(config=DecisionPolicyConfig())
        self.artifact_writer = artifact_writer or DecisionArtifactWriter()
        self.rules = [
            ObservationCoverageRule(min_consecutive_runs=2),
            TestVisibilityRule(min_consecutive_runs=3),
            DependencyDriftRule(min_consecutive_runs=2),
            HotspotConcentrationRule(min_repeated_runs=2),
            TodoAccumulationRule(),
        ]

    def decide(self, context: DecisionContext) -> tuple[ProposalCandidatesArtifact, list[str]]:
        insight_artifact, prior_decisions = self.loader.load(
            repo=context.repo_filter,
            insight_run_id=context.insight_run_id,
            history_limit=context.history_limit,
        )
        candidate_specs = []
        for rule in self.rules:
            candidate_specs.extend(rule.evaluate(insight_artifact.insights))

        policy = DecisionPolicy(
            config=DecisionPolicyConfig(
                max_candidates=context.max_candidates,
                max_candidates_per_family=1,
                cooldown_minutes=context.cooldown_minutes,
            )
        )
        candidates, suppressed = policy.apply(
            candidate_specs=candidate_specs,
            prior_artifacts=prior_decisions,
            generated_at=context.generated_at,
        )
        artifact = ProposalCandidatesArtifact(
            run_id=context.run_id,
            generated_at=context.generated_at,
            source_command=context.source_command,
            repo=DecisionRepoRef(name=insight_artifact.repo.name, path=insight_artifact.repo.path),
            source_insight_run_id=insight_artifact.run_id,
            candidates=candidates,
            suppressed=suppressed,
        )
        return artifact, self.artifact_writer.write(artifact)


def new_decision_context(
    *,
    repo_filter: str | None,
    insight_run_id: str | None,
    history_limit: int,
    max_candidates: int,
    cooldown_minutes: int,
    source_command: str,
) -> DecisionContext:
    generated_at = datetime.now(UTC)
    run_id = f"dec_{generated_at.strftime('%Y%m%dT%H%M%SZ')}_{generated_at.microsecond:06x}"[-31:]
    return DecisionContext(
        repo_filter=repo_filter,
        insight_run_id=insight_run_id,
        history_limit=history_limit,
        max_candidates=max_candidates,
        cooldown_minutes=cooldown_minutes,
        run_id=run_id,
        generated_at=generated_at,
        source_command=source_command,
    )
