from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from control_plane.decision.candidate_builder import CandidateBuilder
from control_plane.decision.artifact_writer import DecisionArtifactWriter
from control_plane.decision.models import DecisionRepoRef, ProposalCandidatesArtifact, SuppressedCandidate
from control_plane.decision.policy import DecisionPolicy, DecisionPolicyConfig
from control_plane.decision.rules.arch_promotion import ArchPromotionRule
from control_plane.decision.rules.backlog_promotion import BacklogPromotionRule
from control_plane.decision.rules.dependency_drift import DependencyDriftRule
from control_plane.decision.rules.execution_health import ExecutionHealthRule
from control_plane.decision.rules.hotspot_concentration import HotspotConcentrationRule
from control_plane.decision.rules.observation_coverage import ObservationCoverageRule
from control_plane.decision.rules.test_visibility import TestVisibilityRule
from control_plane.decision.rules.todo_accumulation import TodoAccumulationRule
from control_plane.decision.suppression import suppressed_candidate
from control_plane.execution import UsageStore
from control_plane.tuning.models import TuningConfig


class DecisionLoaderProtocol(Protocol):
    def load(self, *, repo: str | None, insight_run_id: str | None, history_limit: int):
        ...


_DEFAULT_ALLOWED_FAMILIES: frozenset[str] = frozenset({"observation_coverage", "test_visibility", "dependency_drift", "execution_health_followup"})
ALL_FAMILIES: frozenset[str] = frozenset({"observation_coverage", "test_visibility", "dependency_drift", "execution_health_followup", "hotspot_concentration", "todo_accumulation", "backlog_promotion", "arch_promotion"})


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
    dry_run: bool = False
    allowed_families: frozenset[str] = _DEFAULT_ALLOWED_FAMILIES


def _build_rules(tuning_config: TuningConfig | None) -> list:  # type: ignore[type-arg]
    """Construct decision rules, applying any active tuning overrides."""
    obs_min = 2
    test_min = 3
    drift_min = 2
    if tuning_config is not None:
        obs_min = tuning_config.get_int("observation_coverage", "min_consecutive_runs", 2)
        test_min = tuning_config.get_int("test_visibility", "min_consecutive_runs", 3)
        drift_min = tuning_config.get_int("dependency_drift", "min_consecutive_runs", 2)
    return [
        ObservationCoverageRule(min_consecutive_runs=obs_min),
        TestVisibilityRule(min_consecutive_runs=test_min),
        DependencyDriftRule(min_consecutive_runs=drift_min),
        HotspotConcentrationRule(min_repeated_runs=2),
        TodoAccumulationRule(),
        ExecutionHealthRule(),
        BacklogPromotionRule(),
        ArchPromotionRule(),
    ]


class DecisionEngineService:
    def __init__(
        self,
        *,
        loader: DecisionLoaderProtocol,
        policy: DecisionPolicy | None = None,
        artifact_writer: DecisionArtifactWriter | None = None,
        usage_store: UsageStore | None = None,
        tuning_config: TuningConfig | None = None,
    ) -> None:
        self.loader = loader
        self.policy = policy or DecisionPolicy(config=DecisionPolicyConfig())
        self.artifact_writer = artifact_writer or DecisionArtifactWriter()
        self._usage_store = usage_store
        self.rules = _build_rules(tuning_config)
        self.builder = CandidateBuilder()

    def decide(self, context: DecisionContext) -> tuple[ProposalCandidatesArtifact, list[str]]:
        insight_artifact, prior_decisions = self.loader.load(
            repo=context.repo_filter,
            insight_run_id=context.insight_run_id,
            history_limit=context.history_limit,
        )
        candidate_specs = []
        for rule in self.rules:
            candidate_specs.extend(rule.evaluate(insight_artifact.insights))

        suppressed: list[SuppressedCandidate] = []
        allowed_families = context.allowed_families
        filtered_specs = []
        for spec in candidate_specs:
            if spec.family in allowed_families:
                filtered_specs.append(spec)
                continue
            candidate = self.builder.build(spec)
            suppressed.append(
                suppressed_candidate(
                    dedup_key=candidate.dedup_key,
                    family=candidate.family,
                    subject=candidate.subject,
                    reason="family_deferred_initial_gating",
                    evidence={"allowed_families": sorted(allowed_families)},
                )
            )

        usage_store = self._usage_store or UsageStore()
        remaining = usage_store.remaining_exec_capacity(now=context.generated_at)
        min_remaining = usage_store.settings.min_remaining_exec_for_proposals
        if remaining < min_remaining:
            usage_store.record_proposal_budget_suppression(
                reason="proposal_budget_too_low",
                now=context.generated_at,
                evidence={"remaining_exec_capacity": remaining, "min_required": min_remaining},
            )
            for spec in filtered_specs:
                candidate = self.builder.build(spec)
                suppressed.append(
                    suppressed_candidate(
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        subject=candidate.subject,
                        reason="proposal_budget_too_low",
                        evidence={"remaining_exec_capacity": remaining, "min_required": min_remaining},
                    )
                )
            filtered_specs = []

        policy = DecisionPolicy(
            config=DecisionPolicyConfig(
                max_candidates=context.max_candidates,
                max_candidates_per_family=1,
                cooldown_minutes=context.cooldown_minutes,
            )
        )
        candidates, policy_suppressed = policy.apply(
            candidate_specs=filtered_specs,
            prior_artifacts=prior_decisions,
            generated_at=context.generated_at,
        )
        suppressed.extend(policy_suppressed)
        artifact = ProposalCandidatesArtifact(
            run_id=context.run_id,
            generated_at=context.generated_at,
            source_command=context.source_command,
            dry_run=context.dry_run,
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
    dry_run: bool = False,
    allowed_families: frozenset[str] | None = None,
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
        dry_run=dry_run,
        allowed_families=allowed_families if allowed_families is not None else _DEFAULT_ALLOWED_FAMILIES,
    )
