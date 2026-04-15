from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from control_plane.observer.models import CheckSignal
from control_plane.observer.service import ObserverContext


def latest_matching_file(root: Path, pattern: str) -> Path | None:
    files = sorted(root.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


class CheckSignalCollector:
    def collect(self, context: ObserverContext) -> CheckSignal:
        log_path = latest_matching_file(context.logs_root, "*_test.log")
        if log_path is not None:
            text = log_path.read_text(encoding="utf-8", errors="replace")
            summary = self._extract_summary_line(text)
            status = self._classify_text(text)
            return CheckSignal(
                status=status,
                source=str(log_path),
                observed_at=datetime.fromtimestamp(log_path.stat().st_mtime, tz=UTC),
                summary=summary,
            )
        return self._fallback_discover(context)

    def _fallback_discover(self, context: ObserverContext) -> CheckSignal:
        repo = context.repo_path
        # Check for pytest configuration
        pyproject = repo / "pyproject.toml"
        has_pytest_config = (repo / "pytest.ini").is_file()
        if not has_pytest_config and pyproject.is_file():
            try:
                content = pyproject.read_text(encoding="utf-8", errors="replace")
                has_pytest_config = "[tool.pytest" in content
            except OSError:
                pass

        if not has_pytest_config:
            return CheckSignal(status="no_config")

        try:
            result = subprocess.run(
                ["pytest", "--collect-only", "-q", "--no-header"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=repo,
            )
            if result.returncode not in (0, 5):
                # returncode 5 means no tests collected; other non-zero is an error
                return CheckSignal(status="unknown")

            lines = result.stdout.strip().splitlines()
            # Count test item lines (non-empty lines before the final summary).
            # The summary line typically looks like "X tests collected" or
            # "no tests ran" etc.  Test items contain "::" (e.g. path::test_name).
            count = sum(1 for line in lines if "::" in line and line.strip())
            if count > 0:
                return CheckSignal(
                    status="discoverable",
                    test_count=count,
                    source="pytest --collect-only",
                    summary=f"{count} tests discoverable",
                )
            return CheckSignal(status="unknown")
        except (subprocess.TimeoutExpired, OSError):
            return CheckSignal(status="unknown")

    def _extract_summary_line(self, text: str) -> str | None:
        for line in reversed(text.splitlines()):
            stripped = line.strip()
            if stripped and ("passed" in stripped or "failed" in stripped or "error" in stripped):
                return stripped
        return None

    def _classify_text(self, text: str) -> str:
        lowered = text.lower()
        if re.search(r"\b\d+\s+failed\b", lowered) or "error" in lowered:
            return "failed"
        if re.search(r"\b\d+\s+passed\b", lowered):
            return "passed"
        return "unknown"
