# src/control_plane/spec_director/context_bundle.py
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ContextBundle:
    insight_snapshot: str
    git_log: str
    specs_index: list[dict]
    board_summary: list[dict]
    seed_text: str


class ContextBundleBuilder:
    _MAX_BOARD_TASKS = 50
    _MAX_SPECS = 50
    _MAX_GIT_COMMITS = 30

    def __init__(
        self,
        report_root: Path | None = None,
        max_snapshot_kb: int = 8,
    ) -> None:
        self.report_root = report_root or Path("tools/report/kodo_plane")
        self.max_snapshot_bytes = max_snapshot_kb * 1024

    def build(
        self,
        seed_text: str,
        board_summary: list[dict],
        specs_index: list[dict],
        git_log: str,
    ) -> ContextBundle:
        return ContextBundle(
            insight_snapshot=self._load_insight_snapshot(),
            git_log=git_log,
            specs_index=specs_index[: self._MAX_SPECS],
            board_summary=board_summary[: self._MAX_BOARD_TASKS],
            seed_text=seed_text,
        )

    def _load_insight_snapshot(self) -> str:
        """Load and truncate the most recent autonomy_cycle insights.json."""
        cycle_dir = self.report_root / "autonomy_cycle"
        if not cycle_dir.exists():
            return ""
        runs = sorted(cycle_dir.iterdir(), reverse=True)
        for run in runs:
            insights_path = run / "insights.json"
            if insights_path.exists():
                raw = insights_path.read_text(encoding="utf-8", errors="replace")
                if len(raw.encode()) > self.max_snapshot_bytes:
                    raw = raw.encode()[: self.max_snapshot_bytes].decode("utf-8", errors="replace")
                return raw
        return ""

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
        """Return [{title, status, slug}] for each spec in specs_dir."""
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
