# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations_center.decision.artifact_writer import DecisionArtifactWriter
from operations_center.decision.models import (
    CandidateRationale,
    DecisionRepoRef,
    ProposalCandidate,
    ProposalCandidatesArtifact,
    ProposalOutline,
)
from operations_center.entrypoints.proposer import main as proposer_main
from operations_center.insights.artifact_writer import InsightArtifactWriter
from operations_center.insights.models import InsightRepoRef, RepoInsightsArtifact, SourceSnapshotRef


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


def write_decision_inputs(tmp_path: Path) -> None:
    generated_at = datetime(2026, 3, 31, 13, tzinfo=UTC)
    insight = RepoInsightsArtifact(
        run_id="ins_1",
        generated_at=generated_at,
        source_command="operations-center generate-insights",
        repo=InsightRepoRef(name="operations-center", path=tmp_path / "repo"),
        source_snapshots=[SourceSnapshotRef(run_id="obs_1", observed_at=datetime(2026, 3, 31, 12, tzinfo=UTC))],
        insights=[],
    )
    decision = ProposalCandidatesArtifact(
        run_id="dec_1",
        generated_at=generated_at,
        source_command="operations-center decide-proposals",
        repo=DecisionRepoRef(name="operations-center", path=tmp_path / "repo"),
        source_insight_run_id="ins_1",
        candidates=[
            ProposalCandidate(
                candidate_id="candidate:test_visibility:test_signal:unknown_persistent",
                dedup_key="candidate|test_visibility|test_signal|unknown_persistent",
                family="test_visibility",
                subject="test_signal",
                rationale=CandidateRationale(matched_rules=["rule_a"], suppressed_by=[]),
                proposal_outline=ProposalOutline(
                    title_hint="Improve test signal visibility for operations-center",
                    summary_hint="Add one bounded path for explicit test signal visibility.",
                    labels_hint=["task-kind: goal", "source: proposer"],
                    source_family="test_visibility",
                ),
            )
        ],
        suppressed=[],
    )
    InsightArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "insights").write(insight)
    DecisionArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "decision").write(decision)


def test_propose_from_candidates_cli_writes_artifact_in_dry_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_decision_inputs(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLANE_API_TOKEN", "test-token")

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003, D401
            self.created = []

        def list_issues(self):
            return []

        def create_issue(self, **kwargs):  # noqa: ANN003
            self.created.append(kwargs)
            return {"id": "CP-1"}

        def comment_issue(self, task_id: str, comment_markdown: str) -> None:  # noqa: ARG002
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr("operations_center.entrypoints.proposer.main.PlaneClient", FakeClient)
    monkeypatch.setattr(
        "sys.argv",
        ["propose-from-candidates", "--config", str(write_config(tmp_path)), "--dry-run"],
    )

    proposer_main.main()

    output = capsys.readouterr().out
    assert "Proposal results artifact written:" in output
    assert "Created: 1" in output
