from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from control_plane.config import load_settings
from control_plane.observer.artifact_writer import ObserverArtifactWriter
from control_plane.observer.collectors.dependency_drift import DependencyDriftCollector
from control_plane.observer.collectors.file_hotspots import FileHotspotsCollector
from control_plane.observer.collectors.git_context import GitContextCollector
from control_plane.observer.collectors.recent_commits import RecentCommitsCollector
from control_plane.observer.collectors.test_signal import TestSignalCollector
from control_plane.observer.collectors.todo_signal import TodoSignalCollector
from control_plane.observer.models import (
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal as ObserverTestSignal,
    TodoSignal,
)
from control_plane.observer.service import RepoObserverService, new_observer_context
from control_plane.observer.snapshot_builder import SnapshotBuilder


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
                "  control-plane:",
                "    clone_url: git@github.com:Velascat/ControlPlane.git",
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


def test_snapshot_builder_serializes_with_partial_errors() -> None:
    snapshot = SnapshotBuilder().build(
        run_id="obs_1",
        observed_at=datetime(2026, 3, 31, tzinfo=UTC),
        source_command="control-plane observe-repo",
        repo=RepoContextSnapshot(
            name="control-plane",
            path=Path("/tmp/repo"),
            current_branch="main",
            base_branch="main",
            is_dirty=False,
        ),
        signals=RepoSignalsSnapshot(
            recent_commits=[],
            file_hotspots=[],
            test_signal=ObserverTestSignal(status="unknown"),
            dependency_drift=DependencyDriftSignal(status="not_available"),
            todo_signal=TodoSignal(),
        ),
        collector_errors={"test_signal": "missing"},
    )

    payload = json.loads(snapshot.model_dump_json())
    assert payload["collector_errors"]["test_signal"] == "missing"
    assert payload["repo"]["current_branch"] == "main"


def test_git_context_and_recent_commits_collectors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    commit_file(repo, "src/a.py", "print('a')\n", "Add a")
    commit_file(repo, "src/b.py", "print('b')\n", "Add b")
    (repo / "dirty.txt").write_text("dirty\n")

    settings = load_settings(write_config(tmp_path))
    context = new_observer_context(
        repo_path=repo,
        repo_name="control-plane",
        base_branch="main",
        settings=settings,
        source_command="control-plane observe-repo",
        commit_limit=5,
        hotspot_window=5,
        todo_limit=5,
        logs_root=tmp_path / "logs",
    )

    repo_snapshot = GitContextCollector().collect(context)
    commits = RecentCommitsCollector().collect(context)

    assert repo_snapshot.current_branch == "main"
    assert repo_snapshot.is_dirty is True
    assert len(commits) == 2
    assert commits[0].subject == "Add b"


def test_file_hotspots_and_todo_signal_collectors(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    commit_file(repo, "src/a.py", "# TODO\nprint('a')\n", "Add a")
    commit_file(repo, "src/a.py", "# TODO\n# FIXME\nprint('a2')\n", "Update a")
    commit_file(repo, "src/b.py", "# TODO\nprint('b')\n", "Add b")

    settings = load_settings(write_config(tmp_path))
    context = new_observer_context(
        repo_path=repo,
        repo_name="control-plane",
        base_branch="main",
        settings=settings,
        source_command="control-plane observe-repo",
        commit_limit=5,
        hotspot_window=5,
        todo_limit=5,
        logs_root=tmp_path / "logs",
    )

    hotspots = FileHotspotsCollector().collect(context)
    todo_signal = TodoSignalCollector().collect(context)

    assert hotspots[0].path == "src/a.py"
    assert hotspots[0].touch_count >= 2
    assert todo_signal.todo_count >= 2
    assert todo_signal.fixme_count == 1


def test_test_signal_and_dependency_drift_collectors(tmp_path: Path) -> None:
    logs_root = tmp_path / "logs" / "local"
    logs_root.mkdir(parents=True)
    test_log = logs_root / "20260331T000000_test.log"
    test_log.write_text("...\n12 passed in 0.50s\n")

    report_root = tmp_path / "reports"
    run_dir = report_root / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "dependency_report.json").write_text(
        json.dumps(
            {
                "statuses": [
                    {"key": "kodo", "notes": ["Pinned version differs"]},
                    {"key": "plane", "notes": []},
                ],
                "created_task_ids": ["TASK-1"],
            }
        )
    )

    settings = load_settings(write_config(tmp_path))
    context = new_observer_context(
        repo_path=tmp_path,
        repo_name="control-plane",
        base_branch="main",
        settings=settings,
        source_command="control-plane observe-repo",
        commit_limit=5,
        hotspot_window=5,
        todo_limit=5,
        logs_root=logs_root,
    )

    test_signal = TestSignalCollector().collect(context)
    drift_signal = DependencyDriftCollector().collect(context)

    assert test_signal.status == "passed"
    assert "12 passed" in (test_signal.summary or "")
    assert drift_signal.status == "available"
    assert "actionable_statuses=1" in (drift_signal.summary or "")


def test_observer_service_writes_snapshot_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    commit_file(repo, "src/a.py", "# TODO\nprint('a')\n", "Add a")

    logs_root = tmp_path / "logs" / "local"
    logs_root.mkdir(parents=True)
    settings = load_settings(write_config(tmp_path))
    service = RepoObserverService(
        repo_collector=GitContextCollector(),
        recent_commits_collector=RecentCommitsCollector(),
        file_hotspots_collector=FileHotspotsCollector(),
        test_signal_collector=TestSignalCollector(),
        dependency_drift_collector=DependencyDriftCollector(),
        todo_signal_collector=TodoSignalCollector(),
        snapshot_builder=SnapshotBuilder(),
        artifact_writer=ObserverArtifactWriter(tmp_path / "observer"),
    )
    context = new_observer_context(
        repo_path=repo,
        repo_name="control-plane",
        base_branch="main",
        settings=settings,
        source_command="control-plane observe-repo",
        commit_limit=5,
        hotspot_window=5,
        todo_limit=5,
        logs_root=logs_root,
    )

    snapshot, artifacts = service.observe(context)

    assert isinstance(snapshot, RepoStateSnapshot)
    assert len(artifacts) == 2
    assert Path(artifacts[0]).exists()
    assert Path(artifacts[1]).exists()
