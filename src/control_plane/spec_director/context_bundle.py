# src/control_plane/spec_director/context_bundle.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ContextBundle:
    git_logs: dict[str, str]           # {repo_key: git_log_text}
    specs_index: list[dict]
    recent_done_tasks: list[dict]      # Done tasks from last 14 days
    recent_cancelled_tasks: list[dict]
    open_task_count: int
    seed_text: str
    available_repos: list[str]


class ContextBundleBuilder:
    _MAX_SPECS = 50
    _MAX_GIT_COMMITS = 30
    _MAX_BOARD_TASKS = 50
    _RECENT_DAYS = 14

    def build(
        self,
        seed_text: str,
        board_issues: list[dict],
        specs_index: list[dict],
        git_logs: dict[str, str],
        available_repos: list[str],
    ) -> ContextBundle:
        from datetime import UTC, datetime, timedelta
        cutoff = datetime.now(UTC) - timedelta(days=self._RECENT_DAYS)

        recent_done: list[dict] = []
        recent_cancelled: list[dict] = []
        open_count = 0

        for issue in board_issues[: self._MAX_BOARD_TASKS]:
            state = str((issue.get("state") or {}).get("name", "")).lower()
            updated_raw = issue.get("updated_at") or issue.get("created_at") or ""
            try:
                updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
            except Exception:
                updated = datetime.min.replace(tzinfo=UTC)

            if state == "done" and updated >= cutoff:
                recent_done.append({"name": issue.get("name", "")})
            elif state == "cancelled" and updated >= cutoff:
                recent_cancelled.append({"name": issue.get("name", "")})
            elif state not in {"done", "cancelled"}:
                open_count += 1

        return ContextBundle(
            git_logs=git_logs,
            specs_index=specs_index[: self._MAX_SPECS],
            recent_done_tasks=recent_done,
            recent_cancelled_tasks=recent_cancelled,
            open_task_count=open_count,
            seed_text=seed_text,
            available_repos=available_repos,
        )

    @staticmethod
    def collect_git_log(repo_path: Path, n: int = 30) -> str:
        """Run git log --oneline on *repo_path* and return the output."""
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", f"-{n}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def collect_specs_index(specs_dir: Path) -> list[dict]:
        """Return [{slug, status}] for each spec in specs_dir."""
        from control_plane.spec_director.models import SpecFrontMatter
        index = []
        for p in sorted(specs_dir.glob("*.md")):
            if p.parent.name == "archive":
                continue
            try:
                fm = SpecFrontMatter.from_spec_text(p.read_text())
                index.append({"slug": fm.slug, "status": fm.status})
            except Exception:
                index.append({"slug": p.stem, "status": "unknown"})
        return index
