# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import argparse
from pathlib import Path

from operations_center.config import Settings, load_settings
from operations_center.observer.artifact_writer import ObserverArtifactWriter
from operations_center.observer.collectors.dependency_drift import DependencyDriftCollector
from operations_center.observer.collectors.backlog import BacklogCollector
from operations_center.observer.collectors.execution_health import ExecutionArtifactCollector
from operations_center.observer.collectors.file_hotspots import FileHotspotsCollector
from operations_center.observer.collectors.git_context import GitContextCollector, run_git
from operations_center.observer.collectors.recent_commits import RecentCommitsCollector
from operations_center.observer.collectors.check_signal import CheckSignalCollector
from operations_center.observer.collectors.todo_signal import TodoSignalCollector
from operations_center.observer.service import RepoObserverService, new_observer_context
from operations_center.observer.snapshot_builder import SnapshotBuilder


def normalize_name(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def configured_repo_match(settings: Settings, repo_path: Path) -> tuple[str, str | None]:
    repo_name = normalize_name(repo_path.name)
    for key, repo in settings.repos.items():
        clone_name = repo.clone_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        if repo_name in {normalize_name(key), normalize_name(clone_name)}:
            return key, repo.default_branch
    first_key = next(iter(settings.repos.keys()))
    return first_key, settings.repos[first_key].default_branch


def resolve_repo_path(arg_repo: str | None, settings: Settings) -> tuple[Path, str]:
    if arg_repo:
        candidate = Path(arg_repo).expanduser()
        if candidate.exists():
            return candidate.resolve(), candidate.name
        if arg_repo in settings.repos:
            cwd = Path.cwd().resolve()
            configured_key, _ = configured_repo_match(settings, cwd)
            if configured_key == arg_repo:
                return cwd, cwd.name
            raise ValueError(f"Repo key '{arg_repo}' does not map to a local path here; pass --repo /abs/path")
        raise ValueError(f"Repo path or configured repo key not found: {arg_repo}")
    cwd = Path.cwd().resolve()
    return cwd, cwd.name


def ensure_git_repo(repo_path: Path) -> None:
    try:
        run_git(["rev-parse", "--is-inside-work-tree"], repo_path)
    except Exception as exc:
        raise ValueError(f"Path is not a git repo: {repo_path}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect a read-only repo snapshot for downstream autonomy")
    parser.add_argument("--config", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--base-branch")
    parser.add_argument("--commit-limit", type=int, default=10)
    parser.add_argument("--hotspot-window", type=int, default=25)
    parser.add_argument("--todo-limit", type=int, default=5)
    args = parser.parse_args()

    settings = load_settings(args.config)
    repo_path, repo_name = resolve_repo_path(args.repo, settings)
    ensure_git_repo(repo_path)
    configured_key, configured_base_branch = configured_repo_match(settings, repo_path)
    base_branch = args.base_branch or configured_base_branch

    service = RepoObserverService(
        repo_collector=GitContextCollector(),
        recent_commits_collector=RecentCommitsCollector(),
        file_hotspots_collector=FileHotspotsCollector(),
        test_signal_collector=CheckSignalCollector(),
        dependency_drift_collector=DependencyDriftCollector(),
        todo_signal_collector=TodoSignalCollector(),
        execution_health_collector=ExecutionArtifactCollector(),
        backlog_collector=BacklogCollector(),
        snapshot_builder=SnapshotBuilder(),
        artifact_writer=ObserverArtifactWriter(),
    )
    context = new_observer_context(
        repo_path=repo_path,
        repo_name=configured_key if configured_key else repo_name,
        base_branch=base_branch,
        settings=settings,
        source_command="operations-center observe-repo",
        commit_limit=args.commit_limit,
        hotspot_window=args.hotspot_window,
        todo_limit=args.todo_limit,
        logs_root=Path("logs/local"),
    )
    snapshot, artifacts = service.observe(context)
    print(f"Observer snapshot written: {artifacts[0]}")
    if snapshot.collector_errors:
        print(f"Collector warnings: {len(snapshot.collector_errors)}")


if __name__ == "__main__":
    main()
