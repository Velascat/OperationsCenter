# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from operations_center.entrypoints.insights import main as insights_main
from operations_center.observer.artifact_writer import ObserverArtifactWriter
from operations_center.observer.models import (
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    CheckSignal as ObserverCheckSignal,
    TodoSignal,
)


def _make_snapshot(run_id: str, observed_at: datetime, *, repo_path: Path) -> RepoStateSnapshot:
    return RepoStateSnapshot(
        run_id=run_id,
        observed_at=observed_at,
        source_command="operations-center observe-repo",
        repo=RepoContextSnapshot(
            name="operations-center",
            path=repo_path,
            current_branch="main",
            base_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            recent_commits=[],
            file_hotspots=[],
            test_signal=ObserverCheckSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
        ),
    )


def test_generate_insights_cli_writes_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    observer_root = tmp_path / "tools" / "report" / "operations_center" / "observer"
    ObserverArtifactWriter(observer_root).write(
        _make_snapshot("obs_1", datetime(2026, 3, 31, 12, tzinfo=UTC), repo_path=tmp_path / "repo")
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["generate-insights"])

    insights_main.main()

    output = capsys.readouterr().out
    assert "Insights artifact written:" in output
    artifact_path = Path(output.split("Insights artifact written:", 1)[1].strip().splitlines()[0])
    assert artifact_path.exists()


def test_generate_insights_cli_errors_without_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["generate-insights"])
    with pytest.raises(ValueError):
        insights_main.main()
