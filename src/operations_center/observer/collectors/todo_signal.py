from __future__ import annotations

from collections import Counter
from operations_center.observer.models import TodoFileCount, TodoSignal
from operations_center.observer.service import ObserverContext

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache"}
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".sh",
    ".ini",
    ".cfg",
}


class TodoSignalCollector:
    def collect(self, context: ObserverContext) -> TodoSignal:
        todo_count = 0
        fixme_count = 0
        per_file: Counter[str] = Counter()

        for path in context.repo_path.rglob("*"):
            if path.is_dir():
                if path.name in SKIP_DIRS:
                    continue
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix and path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_count = text.count("TODO") + text.count("FIXME")
            if file_count == 0:
                continue
            todo_count += text.count("TODO")
            fixme_count += text.count("FIXME")
            per_file[str(path.relative_to(context.repo_path)).replace("\\", "/")] = file_count

        return TodoSignal(
            todo_count=todo_count,
            fixme_count=fixme_count,
            top_files=[
                TodoFileCount(path=path, count=count)
                for path, count in per_file.most_common(context.todo_limit)
            ],
        )
