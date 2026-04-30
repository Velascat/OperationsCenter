# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Baseline load, save, and comparison for architecture invariant reports."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def save_baseline(findings: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(findings, indent=2), encoding="utf-8")


def load_baseline(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _finding_key(f: dict[str, Any]) -> str:
    return f"{f.get('path', '')}:{f.get('line', 0)}:{f.get('family', '')}:{f.get('evidence', '')}"


@dataclass
class BaselineComparison:
    new_findings: list[dict[str, Any]] = field(default_factory=list)
    resolved_findings: list[dict[str, Any]] = field(default_factory=list)
    existing_count: int = 0

    @property
    def new_count(self) -> int:
        return len(self.new_findings)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved_findings)


def compare_to_baseline(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> BaselineComparison:
    baseline_keys = {_finding_key(f) for f in baseline}
    current_keys = {_finding_key(f) for f in current}

    new = [f for f in current if _finding_key(f) not in baseline_keys]
    resolved = [f for f in baseline if _finding_key(f) not in current_keys]
    existing = len([f for f in current if _finding_key(f) in baseline_keys])

    return BaselineComparison(
        new_findings=new,
        resolved_findings=resolved,
        existing_count=existing,
    )


__all__ = ["BaselineComparison", "compare_to_baseline", "load_baseline", "save_baseline"]
