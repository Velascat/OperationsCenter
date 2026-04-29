# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import subprocess
from pathlib import Path

from operations_center.observer.models import RepoContextSnapshot
from operations_center.observer.service import ObserverContext


def run_git(args: list[str], repo_path: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


class GitContextCollector:
    def collect(self, context: ObserverContext) -> RepoContextSnapshot:
        current_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], context.repo_path)
        status = run_git(["status", "--porcelain"], context.repo_path)
        return RepoContextSnapshot(
            name=context.repo_name,
            path=context.repo_path.resolve(),
            current_branch=current_branch,
            base_branch=context.base_branch,
            is_dirty=bool(status),
        )
