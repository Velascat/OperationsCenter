# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
import subprocess

from operations_center.observer.models import TypeError, TypeSignal
from operations_center.observer.service import ObserverContext

_MAX_ERRORS = 20


class TypeSignalCollector:
    """Run ty (or mypy as fallback) and collect type errors as a first-class observer signal."""

    def collect(self, context: ObserverContext) -> TypeSignal:
        signal = self._try_ty(context)
        if signal is not None:
            return signal
        return self._try_mypy(context)

    def _try_ty(self, context: ObserverContext) -> TypeSignal | None:
        try:
            result = subprocess.run(
                ["ty", "check", "--output-format", "json", str(context.repo_path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            return TypeSignal(status="unavailable", source="ty_timeout")
        except Exception as exc:
            return TypeSignal(status="unavailable", source=f"ty_error: {exc}")

        return self._parse_ty_output(result.stdout)

    def _try_mypy(self, context: ObserverContext) -> TypeSignal:
        try:
            result = subprocess.run(
                [
                    "mypy",
                    "--output=json",
                    "--no-error-summary",
                    str(context.repo_path),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except FileNotFoundError:
            return TypeSignal(status="unavailable", source="type_checker_not_found")
        except subprocess.TimeoutExpired:
            return TypeSignal(status="unavailable", source="mypy_timeout")
        except Exception as exc:
            return TypeSignal(status="unavailable", source=f"mypy_error: {exc}")

        return self._parse_mypy_output(result.stdout)

    @staticmethod
    def _parse_ty_output(raw: str) -> TypeSignal:
        raw = raw.strip()
        if not raw:
            return TypeSignal(status="clean", error_count=0, source="ty")

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return TypeSignal(status="unavailable", source="ty_parse_error")

        # ty JSON output: {"diagnostics": [...], ...}
        diagnostics = data.get("diagnostics", []) if isinstance(data, dict) else []
        if not isinstance(diagnostics, list):
            return TypeSignal(status="unavailable", source="ty_unexpected_format")

        total = len(diagnostics)
        distinct_file_count = len({item.get("file", "") for item in diagnostics if item.get("file")})

        errors: list[TypeError] = []
        for item in diagnostics[:_MAX_ERRORS]:
            try:
                loc = item.get("range", {}).get("start", {})
                errors.append(
                    TypeError(
                        path=str(item.get("file", "")),
                        line=int(loc.get("line", 0)),
                        col=int(loc.get("character", 0)),
                        code=str(item.get("code", "")),
                        message=str(item.get("message", "")),
                    )
                )
            except Exception:
                continue

        return TypeSignal(
            status="errors" if errors else "clean",
            error_count=total,
            distinct_file_count=distinct_file_count,
            top_errors=errors,
            source="ty",
        )

    @staticmethod
    def _parse_mypy_output(raw: str) -> TypeSignal:
        # mypy --output=json emits one JSON object per line
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return TypeSignal(status="clean", error_count=0, source="mypy")

        all_error_files: set[str] = set()
        error_items: list[dict] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("severity") != "error":
                continue
            f = item.get("file", "")
            if f:
                all_error_files.add(f)
            error_items.append(item)

        errors: list[TypeError] = []
        for item in error_items[:_MAX_ERRORS]:
            try:
                errors.append(
                    TypeError(
                        path=str(item.get("file", "")),
                        line=int(item.get("line", 0)),
                        col=int(item.get("column", 0)),
                        code=str(item.get("error_code", "")),
                        message=str(item.get("message", "")),
                    )
                )
            except Exception:
                continue

        return TypeSignal(
            status="errors" if errors else "clean",
            error_count=len(error_items),
            distinct_file_count=len(all_error_files),
            top_errors=errors,
            source="mypy",
        )
