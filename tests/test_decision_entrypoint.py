# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations_center.entrypoints.decision import main as decision_main
from operations_center.insights.artifact_writer import InsightArtifactWriter
from operations_center.insights.models import DerivedInsight, InsightRepoRef, RepoInsightsArtifact, SourceSnapshotRef


def _write_insight(tmp_path: Path) -> None:
    artifact = RepoInsightsArtifact(
        run_id="ins_1",
        generated_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
        source_command="operations-center generate-insights",
        repo=InsightRepoRef(name="operations-center", path=tmp_path / "repo"),
        source_snapshots=[SourceSnapshotRef(run_id="obs_1", observed_at=datetime(2026, 3, 31, 11, tzinfo=UTC))],
        insights=[
            DerivedInsight(
                insight_id="observation_coverage:test_signal:persistent_unavailable",
                dedup_key="observation_coverage|test_signal|persistent_unavailable",
                kind="observation_coverage",
                subject="test_signal",
                status="present",
                evidence={"signal": "test_signal", "consecutive_snapshots": 3},
                first_seen_at=datetime(2026, 3, 31, 11, tzinfo=UTC),
                last_seen_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
            )
        ],
    )
    InsightArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "insights").write(artifact)


def test_decide_proposals_cli_writes_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _write_insight(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["decide-proposals"])

    decision_main.main()

    output = capsys.readouterr().out
    assert "Proposal candidates artifact written:" in output
    artifact_path = Path(output.split("Proposal candidates artifact written:", 1)[1].strip().splitlines()[0])
    assert artifact_path.exists()


def test_decide_proposals_cli_succeeds_with_zero_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    artifact = RepoInsightsArtifact(
        run_id="ins_2",
        generated_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
        source_command="operations-center generate-insights",
        repo=InsightRepoRef(name="operations-center", path=tmp_path / "repo"),
        source_snapshots=[SourceSnapshotRef(run_id="obs_1", observed_at=datetime(2026, 3, 31, 11, tzinfo=UTC))],
        insights=[],
    )
    InsightArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "insights").write(artifact)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["decide-proposals"])

    decision_main.main()

    output = capsys.readouterr().out
    assert "Candidates emitted: 0" in output


def test_decide_proposals_cli_errors_without_insight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["decide-proposals"])
    with pytest.raises(ValueError):
        decision_main.main()


def test_decide_proposals_cli_accepts_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _write_insight(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["decide-proposals", "--dry-run"])

    decision_main.main()

    output = capsys.readouterr().out
    assert "Proposal candidates artifact written:" in output
