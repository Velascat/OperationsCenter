from __future__ import annotations

import configparser
import itertools
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from operations_center.observer.models import CheckSignal
from operations_center.observer.service import ObserverContext

_TEST_FILE_GLOB_LIMIT = 5


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
        return self._fallback_discovery(context)

    def _fallback_discovery(self, context: ObserverContext) -> CheckSignal:
        repo_root = context.repo_path
        has_config = self._has_pytest_config(repo_root)
        if not has_config:
            return CheckSignal(status="no_config", source="fallback:no_pytest_config")

        try:
            result = subprocess.run(
                ["pytest", "--collect-only", "-q", "--no-header"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=repo_root,
            )
            if result.returncode not in (0, 5):
                # returncode 5 means no tests collected; other non-zero is an error
                return CheckSignal(status="unknown")

            lines = result.stdout.strip().splitlines()
            # Count test item lines (non-empty lines before the final summary).
            # Test items contain "::" (e.g. path::test_name).
            count = sum(1 for line in lines if "::" in line and line.strip())
            if count > 0:
                return CheckSignal(
                    status="discoverable",
                    test_count=count,
                    source="pytest --collect-only",
                    summary=f"{count} tests discoverable",
                )
            # returncode 5 or 0 but no test items found → unknown
            return CheckSignal(status="unknown")
        except (subprocess.TimeoutExpired, OSError):
            return CheckSignal(status="unknown")

    def _has_pytest_config(self, repo_root: Path) -> bool:
        pyproject = repo_root / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8", errors="replace")
                if "[tool.pytest" in text or "[pytest]" in text:
                    return True
            except OSError:
                pass

        pytest_ini = repo_root / "pytest.ini"
        if pytest_ini.is_file():
            return True

        setup_cfg = repo_root / "setup.cfg"
        if setup_cfg.is_file():
            try:
                parser = configparser.ConfigParser()
                parser.read(str(setup_cfg), encoding="utf-8")
                if "tool:pytest" in parser.sections():
                    return True
            except (OSError, configparser.Error):
                pass

        return False

    def _has_test_files(self, repo_root: Path) -> bool:
        candidates = itertools.chain(
            repo_root.rglob("test_*.py"),
            repo_root.rglob("*_test.py"),
        )
        return any(itertools.islice(candidates, _TEST_FILE_GLOB_LIMIT))

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
