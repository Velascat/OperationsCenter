# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from pathlib import Path

from operations_center.decision.models import ProposalCandidatesArtifact
from operations_center.insights.models import RepoInsightsArtifact


class DecisionLoader:
    def __init__(
        self,
        *,
        insights_root: Path | None = None,
        decision_root: Path | None = None,
    ) -> None:
        self.insights_root = insights_root or Path("tools/report/operations_center/insights")
        self.decision_root = decision_root or Path("tools/report/operations_center/decision")

    def load(
        self,
        *,
        repo: str | None,
        insight_run_id: str | None,
        history_limit: int,
    ) -> tuple[RepoInsightsArtifact, list[ProposalCandidatesArtifact]]:
        insights = self._all_insights()
        if repo:
            repo_normalized = repo.strip().lower()
            insights = [
                artifact
                for artifact in insights
                if artifact.repo.name.strip().lower() == repo_normalized
                or str(artifact.repo.path).strip().lower() == repo_normalized
            ]
        if insight_run_id:
            matching = [artifact for artifact in insights if artifact.run_id == insight_run_id]
            if not matching:
                raise ValueError(f"Insight run id not found: {insight_run_id}")
            current = matching[0]
        else:
            if not insights:
                raise ValueError("No insight artifacts found for the requested repo/context")
            current = insights[0]

        prior_decisions = [
            artifact
            for artifact in self._all_decisions()
            if artifact.repo.path == current.repo.path and artifact.source_insight_run_id != current.run_id
        ][:history_limit]
        return current, prior_decisions

    def _all_insights(self) -> list[RepoInsightsArtifact]:
        artifacts = [RepoInsightsArtifact.model_validate_json(path.read_text(encoding="utf-8")) for path in self.insights_root.glob("*/repo_insights.json")]
        return sorted(artifacts, key=lambda artifact: artifact.generated_at, reverse=True)

    def _all_decisions(self) -> list[ProposalCandidatesArtifact]:
        artifacts = [
            ProposalCandidatesArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.decision_root.glob("*/proposal_candidates.json")
        ]
        return sorted(artifacts, key=lambda artifact: artifact.generated_at, reverse=True)
