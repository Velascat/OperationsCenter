# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from pathlib import Path

from operations_center.decision.models import ProposalCandidatesArtifact
from operations_center.insights.models import RepoInsightsArtifact


class ProposalCandidateLoader:
    def __init__(
        self,
        *,
        decision_root: Path | None = None,
        insights_root: Path | None = None,
    ) -> None:
        self.decision_root = decision_root or Path("tools/report/operations_center/decision")
        self.insights_root = insights_root or Path("tools/report/operations_center/insights")

    def load(
        self,
        *,
        repo: str | None,
        decision_run_id: str | None,
    ) -> tuple[ProposalCandidatesArtifact, RepoInsightsArtifact]:
        decisions = self._all_decisions()
        if repo:
            repo_normalized = repo.strip().lower()
            decisions = [
                artifact
                for artifact in decisions
                if artifact.repo.name.strip().lower() == repo_normalized
                or str(artifact.repo.path).strip().lower() == repo_normalized
            ]
        if decision_run_id:
            matches = [artifact for artifact in decisions if artifact.run_id == decision_run_id]
            if not matches:
                raise ValueError(f"Decision run id not found: {decision_run_id}")
            decision = matches[0]
        else:
            if not decisions:
                raise ValueError("No decision artifacts found for the requested repo/context")
            decision = decisions[0]
        insight = self._load_insight(decision.source_insight_run_id)
        return decision, insight

    def _all_decisions(self) -> list[ProposalCandidatesArtifact]:
        artifacts = [
            ProposalCandidatesArtifact.model_validate_json(path.read_text(encoding="utf-8"))
            for path in self.decision_root.glob("*/proposal_candidates.json")
        ]
        return sorted(artifacts, key=lambda artifact: artifact.generated_at, reverse=True)

    def _load_insight(self, run_id: str) -> RepoInsightsArtifact:
        path = self.insights_root / run_id / "repo_insights.json"
        if not path.exists():
            raise ValueError(f"Insight artifact not found for decision source run: {run_id}")
        return RepoInsightsArtifact.model_validate_json(path.read_text(encoding="utf-8"))
