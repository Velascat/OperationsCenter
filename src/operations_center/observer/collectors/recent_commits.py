# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import datetime

from operations_center.observer.collectors.git_context import run_git
from operations_center.observer.models import CommitMetadata
from operations_center.observer.service import ObserverContext


class RecentCommitsCollector:
    def collect(self, context: ObserverContext) -> list[CommitMetadata]:
        raw = run_git(
            [
                "log",
                f"-n{context.commit_limit}",
                "--date=iso-strict",
                "--pretty=format:%h%x1f%an%x1f%aI%x1f%s",
            ],
            context.repo_path,
        )
        commits: list[CommitMetadata] = []
        for line in raw.splitlines():
            parts = line.split("\x1f")
            if len(parts) != 4:
                continue
            sha_short, author, timestamp, subject = parts
            commits.append(
                CommitMetadata(
                    sha_short=sha_short,
                    author=author,
                    timestamp=datetime.fromisoformat(timestamp),
                    subject=subject,
                )
            )
        return commits
