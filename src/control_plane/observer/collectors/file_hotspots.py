from __future__ import annotations

from collections import Counter

from control_plane.observer.collectors.git_context import run_git
from control_plane.observer.models import FileHotspot
from control_plane.observer.service import ObserverContext


class FileHotspotsCollector:
    def collect(self, context: ObserverContext) -> list[FileHotspot]:
        raw = run_git(
            [
                "log",
                f"-n{context.hotspot_window}",
                "--name-only",
                "--pretty=format:",
            ],
            context.repo_path,
        )
        counts: Counter[str] = Counter()
        for line in raw.splitlines():
            path = line.strip()
            if not path:
                continue
            counts[path] += 1
        return [
            FileHotspot(path=path, touch_count=count)
            for path, count in counts.most_common(context.todo_limit)
        ]
