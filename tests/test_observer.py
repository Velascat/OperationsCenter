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
from control_plane.observer.collectors.check_signal import CheckSignalCollector
from control_plane.observer.collectors.todo_signal import TodoSignalCollector
from control_plane.observer.models import (
    ArchitectureSignal,
    BenchmarkSignal,
    DependencyDriftSignal,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    SecuritySignal,
    CheckSignal as ObserverCheckSignal,
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
            test_signal=ObserverCheckSignal(status="unknown"),
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

    test_signal = CheckSignalCollector().collect(context)
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
        test_signal_collector=CheckSignalCollector(),
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


# ---------------------------------------------------------------------------
# Phase 5 – new signal models and service extension
# ---------------------------------------------------------------------------


def test_new_signal_models_can_be_imported() -> None:
    """ArchitectureSignal, BenchmarkSignal, SecuritySignal are importable."""
    arch = ArchitectureSignal(status="healthy")
    bench = BenchmarkSignal(status="nominal")
    sec = SecuritySignal(status="clean")

    assert arch.status == "healthy"
    assert arch.circular_dependencies == []
    assert arch.max_import_depth is None
    assert arch.coupling_score is None

    assert bench.status == "nominal"
    assert bench.benchmark_count == 0
    assert bench.regressions == []

    assert sec.status == "clean"
    assert sec.advisory_count == 0
    assert sec.critical_count == 0
    assert sec.high_count == 0


def test_repo_signals_snapshot_new_fields_default_to_unavailable() -> None:
    """RepoSignalsSnapshot can be built with minimal required args; new fields default to unavailable."""
    snapshot = RepoSignalsSnapshot(
        test_signal=ObserverCheckSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="not_available"),
        todo_signal=TodoSignal(),
    )

    assert snapshot.architecture_signal.status == "unavailable"
    assert snapshot.benchmark_signal.status == "unavailable"
    assert snapshot.security_signal.status == "unavailable"


def _make_service(tmp_path: Path, **extra_collectors) -> tuple[RepoObserverService, "ObserverContext"]:  # type: ignore[name-defined]  # noqa: F821
    """Helper: build a minimal service + context for unit-level service tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    init_git_repo(repo)
    commit_file(repo, "src/a.py", "print('a')\n", "Add a")

    logs_root = tmp_path / "logs" / "local"
    logs_root.mkdir(parents=True)
    settings = load_settings(write_config(tmp_path))

    service = RepoObserverService(
        repo_collector=GitContextCollector(),
        recent_commits_collector=RecentCommitsCollector(),
        file_hotspots_collector=FileHotspotsCollector(),
        test_signal_collector=CheckSignalCollector(),
        dependency_drift_collector=DependencyDriftCollector(),
        todo_signal_collector=TodoSignalCollector(),
        snapshot_builder=SnapshotBuilder(),
        artifact_writer=ObserverArtifactWriter(tmp_path / "observer"),
        **extra_collectors,
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
    return service, context


class _FixedCollector:
    """Collector that always returns a pre-set value."""

    def __init__(self, value):
        self._value = value

    def collect(self, context):
        return self._value


class _FailingCollector:
    """Collector that always raises."""

    def __init__(self, message: str = "boom"):
        self._message = message

    def collect(self, context):
        raise RuntimeError(self._message)


def test_observer_service_backward_compatible_without_new_collectors(tmp_path: Path) -> None:
    """Service instantiates and runs correctly with no new Phase 5 collectors."""
    service, context = _make_service(tmp_path)

    snapshot, _ = service.observe(context)

    assert snapshot.signals.architecture_signal.status == "unavailable"
    assert snapshot.signals.benchmark_signal.status == "unavailable"
    assert snapshot.signals.security_signal.status == "unavailable"


def test_observer_service_with_new_collectors_provided(tmp_path: Path) -> None:
    """When new collectors are provided, their return values appear in the snapshot."""
    arch = ArchitectureSignal(status="warnings", max_import_depth=10, coupling_score=0.42)
    bench = BenchmarkSignal(status="regression", benchmark_count=5, regressions=["bench_foo"])
    sec = SecuritySignal(status="advisories", advisory_count=3, critical_count=1, high_count=2)

    service, context = _make_service(
        tmp_path,
        architecture_signal_collector=_FixedCollector(arch),
        benchmark_signal_collector=_FixedCollector(bench),
        security_signal_collector=_FixedCollector(sec),
    )

    snapshot, _ = service.observe(context)

    assert snapshot.signals.architecture_signal.status == "warnings"
    assert snapshot.signals.architecture_signal.max_import_depth == 10
    assert snapshot.signals.architecture_signal.coupling_score == 0.42

    assert snapshot.signals.benchmark_signal.status == "regression"
    assert snapshot.signals.benchmark_signal.benchmark_count == 5
    assert snapshot.signals.benchmark_signal.regressions == ["bench_foo"]

    assert snapshot.signals.security_signal.status == "advisories"
    assert snapshot.signals.security_signal.advisory_count == 3
    assert snapshot.signals.security_signal.critical_count == 1
    assert snapshot.signals.security_signal.high_count == 2


def test_observer_service_failing_collector_records_error_and_returns_unavailable(tmp_path: Path) -> None:
    """When a new collector raises, the signal is 'unavailable' and the error is recorded."""
    service, context = _make_service(
        tmp_path,
        architecture_signal_collector=_FailingCollector("arch exploded"),
        benchmark_signal_collector=_FailingCollector("bench exploded"),
        security_signal_collector=_FailingCollector("sec exploded"),
    )

    snapshot, _ = service.observe(context)

    assert snapshot.signals.architecture_signal.status == "unavailable"
    assert snapshot.signals.benchmark_signal.status == "unavailable"
    assert snapshot.signals.security_signal.status == "unavailable"

    assert snapshot.collector_errors.get("architecture_signal") == "arch exploded"
    assert snapshot.collector_errors.get("benchmark_signal") == "bench exploded"
    assert snapshot.collector_errors.get("security_signal") == "sec exploded"


def test_observer_service_partial_new_collectors(tmp_path: Path) -> None:
    """Only some new collectors provided; others default to unavailable without error."""
    arch = ArchitectureSignal(status="healthy")
    service, context = _make_service(
        tmp_path,
        architecture_signal_collector=_FixedCollector(arch),
        # benchmark and security collectors deliberately omitted
    )

    snapshot, _ = service.observe(context)

    assert snapshot.signals.architecture_signal.status == "healthy"
    assert snapshot.signals.benchmark_signal.status == "unavailable"
    assert snapshot.signals.security_signal.status == "unavailable"
    # No errors for absent collectors
    assert "benchmark_signal" not in snapshot.collector_errors
    assert "security_signal" not in snapshot.collector_errors
