# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
import subprocess

from operations_center.observer.models import LintSignal, LintViolation
from operations_center.observer.service import ObserverContext

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
        return self._parse_ruff_output(raw)

    @staticmethod
    def _parse_ruff_output(raw: str) -> LintSignal:
        if not raw:
            return LintSignal(status="clean", violation_count=0, source="ruff")

        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return LintSignal(status="unavailable", source="ruff_parse_error")

        if not isinstance(items, list):
            return LintSignal(status="unavailable", source="ruff_unexpected_format")

        distinct_file_count = len({item.get("filename", "") for item in items if item.get("filename")})

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
            distinct_file_count=distinct_file_count,
            top_violations=violations,
            source="ruff",
        )
