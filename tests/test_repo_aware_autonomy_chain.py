# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from operations_center.config import load_settings
from operations_center.decision.artifact_writer import DecisionArtifactWriter
from operations_center.decision.loader import DecisionLoader
from operations_center.decision.service import DecisionEngineService, new_decision_context
from operations_center.insights.artifact_writer import InsightArtifactWriter
from operations_center.insights.derivers.commit_activity import CommitActivityDeriver
from operations_center.insights.derivers.dependency_drift import DependencyDriftDeriver
from operations_center.insights.derivers.dirty_tree import DirtyTreeDeriver
from operations_center.insights.derivers.file_hotspots import FileHotspotsDeriver
from operations_center.insights.derivers.observation_coverage import ObservationCoverageDeriver
from operations_center.insights.derivers.test_continuity import TestContinuityDeriver as ContinuityDeriver
from operations_center.insights.derivers.todo_concentration import TodoConcentrationDeriver
from operations_center.insights.loader import SnapshotLoader
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.insights.service import InsightEngineService, new_generation_context
from operations_center.observer.artifact_writer import ObserverArtifactWriter
from operations_center.observer.collectors.dependency_drift import DependencyDriftCollector
from operations_center.observer.collectors.file_hotspots import FileHotspotsCollector
from operations_center.observer.collectors.git_context import GitContextCollector
from operations_center.observer.collectors.recent_commits import RecentCommitsCollector
from operations_center.observer.collectors.check_signal import CheckSignalCollector
from operations_center.observer.collectors.todo_signal import TodoSignalCollector
from operations_center.observer.service import RepoObserverService, new_observer_context
from operations_center.observer.snapshot_builder import SnapshotBuilder
from operations_center.proposer.artifact_writer import ProposerArtifactWriter
from operations_center.proposer.candidate_integration import (
    CandidateProposerIntegrationService,
    new_proposer_integration_context,
)
from operations_center.proposer.candidate_loader import ProposalCandidateLoader
from operations_center.execution import UsageStore
from operations_center.proposer.guardrail_adapter import ProposerGuardrailAdapter


class FakePlaneClient:
    def __init__(self) -> None:
        self.issues: list[dict[str, object]] = []
        self.created: list[dict[str, object]] = []
        self.comments: list[tuple[str, str]] = []

    def list_issues(self) -> list[dict[str, object]]:
        return list(self.issues)

    def create_issue(
        self,
        *,
        name: str,
        description: str,
        state: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict[str, object]:
        issue = {
            "id": f"ISSUE-{len(self.created) + 1}",
            "name": name,
            "description": description,
            "state": {"name": state or "Backlog"},
            "labels": [{"name": label} for label in (label_names or [])],
        }
        self.created.append(issue)
        self.issues.append(issue)
        return issue

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        self.comments.append((task_id, comment_markdown))


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "plane:",
                "  base_url: http://plane.local",
                "  api_token_env: PLANE_API_TOKEN",
                "  workspace_slug: ws",
                "  project_id: proj",
                "git: {}",
                "kodo: {}",
                "repos:",
                "  operations-center:",
                "    clone_url: git@github.com:Velascat/OperationsCenter.git",
                "    default_branch: main",
                f"report_root: {tmp_path / 'reports'}",
            ]
        )
    )
    return config_path


def init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)


def commit_file(repo: Path, name: str, content: str, message: str) -> None:
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-m", message, "--author", "Test User <test@example.com>"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_repo_aware_autonomy_chain_creates_provenance_rich_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    commit_file(repo, "src/watcher.py", "# TODO: tighten observer coverage\nprint('watcher')\n", "Add watcher file")

    settings = load_settings(write_config(tmp_path))
    logs_root = tmp_path / "logs" / "local"
    logs_root.mkdir(parents=True)

    observer_root = tmp_path / "tools" / "report" / "operations_center" / "observer"
    observer_service = RepoObserverService(
        repo_collector=GitContextCollector(),
        recent_commits_collector=RecentCommitsCollector(),
        file_hotspots_collector=FileHotspotsCollector(),
        test_signal_collector=CheckSignalCollector(),
        dependency_drift_collector=DependencyDriftCollector(),
        todo_signal_collector=TodoSignalCollector(),
        snapshot_builder=SnapshotBuilder(),
        artifact_writer=ObserverArtifactWriter(observer_root),
    )

    for observed_at in [
        datetime(2026, 3, 31, 12, tzinfo=UTC),
        datetime(2026, 3, 31, 13, tzinfo=UTC),
    ]:
        context = new_observer_context(
            repo_path=repo,
            repo_name="operations-center",
            base_branch="main",
            settings=settings,
            source_command="operations-center observe-repo",
            commit_limit=5,
            hotspot_window=10,
            todo_limit=5,
            logs_root=logs_root,
        )
        context = context.__class__(**{**context.__dict__, "observed_at": observed_at, "run_id": f"obs_{observed_at.strftime('%H%M%S')}"})
        observer_service.observe(context)

    normalizer = InsightNormalizer()
    insights_root = tmp_path / "tools" / "report" / "operations_center" / "insights"
    insight_service = InsightEngineService(
        loader=SnapshotLoader(observer_root),
        derivers=[
                DirtyTreeDeriver(normalizer),
                CommitActivityDeriver(normalizer),
                FileHotspotsDeriver(normalizer),
                ContinuityDeriver(normalizer),
                DependencyDriftDeriver(normalizer),
                TodoConcentrationDeriver(normalizer),
                ObservationCoverageDeriver(normalizer),
        ],
        artifact_writer=InsightArtifactWriter(insights_root),
    )
    insight_artifact, insight_paths = insight_service.generate(
        new_generation_context(
            repo_filter="operations-center",
            snapshot_run_id=None,
            history_limit=5,
            source_command="operations-center generate-insights",
        )
    )

    decision_root = tmp_path / "tools" / "report" / "operations_center" / "decision"
    decision_service = DecisionEngineService(
        loader=DecisionLoader(insights_root=insights_root, decision_root=decision_root),
        artifact_writer=DecisionArtifactWriter(decision_root),
        usage_store=UsageStore(tmp_path / "usage.json"),
    )
    decision_artifact, decision_paths = decision_service.decide(
        new_decision_context(
            repo_filter="operations-center",
            insight_run_id=insight_artifact.run_id,
            history_limit=5,
            max_candidates=3,
            cooldown_minutes=120,
            source_command="operations-center decide-proposals",
        )
    )

    plane_client = FakePlaneClient()
    proposer_root = tmp_path / "tools" / "report" / "operations_center" / "proposer"
    proposer_service = CandidateProposerIntegrationService(
        settings=settings,
        client=plane_client,  # type: ignore[arg-type]
        loader=ProposalCandidateLoader(decision_root=decision_root, insights_root=insights_root),
        guardrails=ProposerGuardrailAdapter(proposer_root=proposer_root, usage_store=UsageStore(tmp_path / "usage.json")),
        artifact_writer=ProposerArtifactWriter(proposer_root),
    )
    proposal_artifact, proposal_paths = proposer_service.run(
        new_proposer_integration_context(
            repo_filter="operations-center",
            decision_run_id=decision_artifact.run_id,
            max_create=2,
            dry_run=False,
            source_command="operations-center propose-from-candidates",
        )
    )

    assert Path(insight_paths[0]).exists()
    assert Path(decision_paths[0]).exists()
    assert Path(proposal_paths[0]).exists()
    assert any(candidate.family == "observation_coverage" for candidate in decision_artifact.candidates)
    assert len(proposal_artifact.created) >= 1
    assert any(item.family == "observation_coverage" for item in proposal_artifact.created)
    matching_issues = [
        issue
        for issue in plane_client.created
        if {"source-family: observation_coverage", "source: autonomy", "source: propose"}
        <= {label["name"] for label in issue["labels"]}  # type: ignore[index]
    ]
    assert matching_issues
    description = str(matching_issues[0]["description"])
    assert "## Provenance" in description
    assert f"insight_run_id: {insight_artifact.run_id}" in description
    assert f"decision_run_id: {decision_artifact.run_id}" in description
    assert "source: autonomy-proposer" in description
