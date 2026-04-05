from __future__ import annotations

import json
import subprocess

from control_plane.observer.models import LintSignal, LintViolation
from control_plane.observer.service import ObserverContext

_MAX_VIOLATIONS = 20


class LintSignalCollector:
    """Run ruff check and collect lint violations as a first-class observer signal."""

    def collect(self, context: ObserverContext) -> LintSignal:
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", "--quiet", str(context.repo_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            return LintSignal(status="unavailable", source="ruff_not_found")
        except subprocess.TimeoutExpired:
            return LintSignal(status="unavailable", source="ruff_timeout")
        except Exception as exc:
            return LintSignal(status="unavailable", source=f"ruff_error: {exc}")

        raw = result.stdout.strip()
        if not raw:
            return LintSignal(status="clean", violation_count=0, source="ruff")

        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return LintSignal(status="unavailable", source="ruff_parse_error")

        if not isinstance(items, list):
            return LintSignal(status="unavailable", source="ruff_unexpected_format")

        violations: list[LintViolation] = []
        for item in items[:_MAX_VIOLATIONS]:
            try:
                loc = item.get("location", {})
                violations.append(
                    LintViolation(
                        path=str(item.get("filename", "")),
                        line=int(loc.get("row", 0)),
                        col=int(loc.get("column", 0)),
                        code=str(item.get("code", "")),
                        message=str(item.get("message", "")),
                    )
                )
            except Exception:
                continue

        return LintSignal(
            status="violations" if violations else "clean",
            violation_count=len(items),
            top_violations=violations,
            source="ruff",
        )
