from __future__ import annotations

import re
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
