# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from operations_center.decision.candidate_builder import CandidateBuilder
from operations_center.decision.artifact_writer import DecisionArtifactWriter
from operations_center.decision.models import DecisionRepoRef, ProposalCandidatesArtifact, SuppressedCandidate
from operations_center.decision.policy import DecisionPolicy, DecisionPolicyConfig
from operations_center.decision.rules.arch_promotion import ArchPromotionRule
from operations_center.decision.rules.coverage_gap import CoverageGapRule
from operations_center.decision.rules.lint_cluster import LintClusterRule
from operations_center.decision.rules.backlog_promotion import BacklogPromotionRule
from operations_center.decision.rules.dependency_drift import DependencyDriftRule
from operations_center.decision.rules.execution_health import ExecutionHealthRule
from operations_center.decision.rules.hotspot_concentration import HotspotConcentrationRule
from operations_center.decision.rules.ci_pattern import CIPatternRule
from operations_center.decision.rules.lint_fix import LintFixRule
from operations_center.decision.rules.validation_pattern import ValidationPatternRule
from operations_center.decision.rules.observation_coverage import ObservationCoverageRule
from operations_center.decision.rules.type_improvement import TypeImprovementRule
from operations_center.decision.rules.test_visibility import TestVisibilityRule
from operations_center.decision.rules.todo_accumulation import TodoAccumulationRule
from operations_center.decision.chain_policy import ChainPolicy
from operations_center.decision.suppression import suppressed_candidate
from operations_center.execution import UsageStore
from operations_center.tuning.models import TuningConfig


class DecisionLoaderProtocol(Protocol):
    def load(self, *, repo: str | None, insight_run_id: str | None, history_limit: int):
        ...


_DEFAULT_ALLOWED_FAMILIES: frozenset[str] = frozenset({"observation_coverage", "test_visibility", "dependency_drift", "execution_health_followup", "lint_fix", "type_fix", "validation_pattern_followup"})
ALL_FAMILIES: frozenset[str] = frozenset({"observation_coverage", "test_visibility", "dependency_drift", "execution_health_followup", "lint_fix", "type_fix", "validation_pattern_followup", "ci_pattern", "hotspot_concentration", "todo_accumulation", "backlog_promotion", "arch_promotion", "coverage_gap", "lint_cluster"})


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
    max_proposals_per_24h: int = 10
    max_changed_files: int = 30
    proposer_root: Path = field(default_factory=lambda: Path("tools/report/operations_center/proposer"))
    feedback_root: Path = field(default_factory=lambda: Path("state/proposal_feedback"))


def _build_rules(tuning_config: TuningConfig | None) -> list:  # type: ignore[type-arg]
    """Construct decision rules, applying any active tuning overrides."""
    obs_min = 2
    test_min = 3
    drift_min = 2
    if tuning_config is not None:
        obs_min = tuning_config.get_int("observation_coverage", "min_consecutive_runs", 2)
        test_min = tuning_config.get_int("test_visibility", "min_consecutive_runs", 3)
        drift_min = tuning_config.get_int("dependency_drift", "min_consecutive_runs", 2)
    lint_min = 5
    if tuning_config is not None:
        lint_min = tuning_config.get_int("lint_fix", "min_violations", 5)
    type_min = 3
    if tuning_config is not None:
        type_min = tuning_config.get_int("type_fix", "min_errors", 3)
    return [
        ObservationCoverageRule(min_consecutive_runs=obs_min),
        TestVisibilityRule(min_consecutive_runs=test_min),
        DependencyDriftRule(min_consecutive_runs=drift_min),
        HotspotConcentrationRule(min_repeated_runs=2),
        TodoAccumulationRule(),
        ExecutionHealthRule(),
        LintFixRule(min_violations=lint_min),
        TypeImprovementRule(min_errors=type_min),
        CIPatternRule(),
        ValidationPatternRule(),
        BacklogPromotionRule(),
        ArchPromotionRule(),
        CoverageGapRule(),
        LintClusterRule(),
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

        # Velocity cap: suppress all if too many proposals were created in the last 24h
        recent_count = self._count_proposals_last_24h(context)
        if recent_count >= context.max_proposals_per_24h:
            for spec in filtered_specs:
                candidate = self.builder.build(spec)
                suppressed.append(
                    suppressed_candidate(
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        subject=candidate.subject,
                        reason="velocity_cap_reached",
                        evidence={
                            "proposals_last_24h": recent_count,
                            "max_proposals_per_24h": context.max_proposals_per_24h,
                        },
                    )
                )
            filtered_specs = []

        # Staleness check: suppress dedup_keys with unresolved proposals past expiry
        stale_keys = self._stale_open_dedup_keys(context, prior_decisions)
        if stale_keys:
            live_specs = []
            for spec in filtered_specs:
                candidate = self.builder.build(spec)
                if candidate.dedup_key in stale_keys:
                    suppressed.append(
                        suppressed_candidate(
                            dedup_key=candidate.dedup_key,
                            family=candidate.family,
                            subject=candidate.subject,
                            reason="proposal_stale_open",
                            evidence={"stale_dedup_key": candidate.dedup_key},
                        )
                    )
                else:
                    live_specs.append(spec)
            filtered_specs = live_specs

        # Chain policy: suppress downstream families when upstream prerequisites are active.
        chain_policy = ChainPolicy(cooldown_minutes=context.cooldown_minutes)
        filtered_specs, chain_suppressed = chain_policy.apply(
            specs=filtered_specs,
            prior_artifacts=prior_decisions,
            generated_at=context.generated_at,
        )
        suppressed.extend(chain_suppressed)

        policy = DecisionPolicy(
            config=DecisionPolicyConfig(
                max_candidates=context.max_candidates,
                max_candidates_per_family=1,
                cooldown_minutes=context.cooldown_minutes,
                max_changed_files=context.max_changed_files,
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


    def _count_proposals_last_24h(self, context: DecisionContext) -> int:
        """Count proposals created in the proposer stage within the last 24 hours."""
        cutoff = context.generated_at - timedelta(hours=24)
        count = 0
        proposer_root = context.proposer_root
        if not proposer_root.exists():
            return 0
        for results_file in proposer_root.glob("*/proposal_results.json"):
            try:
                data = json.loads(results_file.read_text())
            except Exception:
                continue
            if data.get("dry_run"):
                continue
            ts_raw = data.get("generated_at", "")
            try:
                generated_at = datetime.fromisoformat(str(ts_raw))
            except Exception:
                continue
            if generated_at < cutoff:
                continue
            created = data.get("created", [])
            if isinstance(created, list):
                count += len(created)
        return count

    def _stale_open_dedup_keys(
        self,
        context: DecisionContext,
        prior_decisions: list,
    ) -> set[str]:
        """Return dedup_keys for proposals that were created but are still open past expiry."""
        # Build dedup_key → (plane_issue_id, proposed_at, expires_after_runs) from prior decisions
        dedup_to_proposed: dict[str, tuple[str, datetime, int]] = {}
        for artifact in prior_decisions:
            for candidate in artifact.candidates:
                if candidate.dedup_key not in dedup_to_proposed:
                    dedup_to_proposed[candidate.dedup_key] = (
                        "",  # plane_issue_id unknown from decision artifact alone
                        artifact.generated_at,
                        candidate.expires_after_runs,
                    )

        if not dedup_to_proposed:
            return set()

        # Enrich with plane_issue_id from proposer artifacts
        dedup_to_issue: dict[str, str] = {}
        proposer_root = context.proposer_root
        if proposer_root.exists():
            for results_file in proposer_root.glob("*/proposal_results.json"):
                try:
                    data = json.loads(results_file.read_text())
                except Exception:
                    continue
                created = data.get("created", [])
                if not isinstance(created, list):
                    continue
                for item in created:
                    if isinstance(item, dict):
                        dk = str(item.get("dedup_key", ""))
                        issue_id = str(item.get("plane_issue_id", ""))
                        if dk and issue_id:
                            dedup_to_issue[dk] = issue_id

        # Load resolved issue_ids from feedback records
        resolved_issue_ids: set[str] = set()
        feedback_root = context.feedback_root
        if feedback_root.exists():
            for fb_file in feedback_root.glob("*.json"):
                try:
                    record = json.loads(fb_file.read_text())
                except Exception:
                    continue
                task_id = str(record.get("task_id", ""))
                outcome = str(record.get("outcome", ""))
                if task_id and outcome in ("merged", "escalated"):
                    resolved_issue_ids.add(task_id)

        stale: set[str] = set()
        for dedup_key, (_, proposed_at, expires_after_runs) in dedup_to_proposed.items():
            expiry = proposed_at + timedelta(days=expires_after_runs * 2)
            if expiry >= context.generated_at:
                continue  # not yet expired
            issue_id = dedup_to_issue.get(dedup_key, "")
            if not issue_id:
                continue  # was never actually created in Plane
            if issue_id in resolved_issue_ids:
                continue  # already resolved (merged or escalated)
            stale.add(dedup_key)
        return stale


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
