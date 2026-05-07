# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Count code-quality suppression markers added in a kodo diff.

Cited by `docs/design/autonomy/autonomy_gaps.md` S5-10 (Kodo Quality Erosion
Detection). The intent: if kodo's output keeps reaching for `# noqa` /
`# type: ignore` to silence linters and type checkers instead of fixing
the underlying issue, that's a signal that the team config is degrading
or the goal text is overconstrained.

Read-only: this module collects counts; it does not act on them. Per the
anti-collapse invariant, nothing here imports `behavior_calibration` or
prescribes runtime changes — the count becomes a finding for downstream
observability.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Markers we count as "the lint just got muted". Patterns are deliberate:
# match only ADDED lines (lines starting with `+ ` in unified diff format,
# excluding the file-header `+++` line).
_SUPPRESSION_PATTERNS = (
    r"#\s*noqa\b",
    r"#\s*type:\s*ignore\b",
    r"#\s*pragma:\s*no\s*cover\b",
    r"#\s*pylint:\s*disable\b",
    r"#\s*ruff:\s*noqa\b",
    r"@pytest\.mark\.skip\b",
    r"@pytest\.mark\.xfail\b",
)
_ADDED_LINE_RE = re.compile(r"^\+(?!\+\+).*", re.MULTILINE)


@dataclass(frozen=True)
class SuppressionCount:
    """Counts of each suppression kind added in a diff."""
    total: int
    by_kind: dict[str, int]


def _count_quality_suppressions(diff_text: str) -> SuppressionCount:
    """Return how many quality-suppression markers a diff *adds*.

    Only added lines (`+ ...`) are counted; existing suppressions in the
    base aren't held against the diff. Headers (`+++ b/file`) are excluded.
    """
    if not diff_text:
        return SuppressionCount(total=0, by_kind={})

    by_kind: dict[str, int] = {}
    total = 0
    for added in _ADDED_LINE_RE.findall(diff_text):
        for pat in _SUPPRESSION_PATTERNS:
            if re.search(pat, added):
                # Use the literal pattern as the key (without regex syntax)
                key = re.sub(r"[\\^$.*+?()|\[\]{}]", "", pat).strip()
                by_kind[key] = by_kind.get(key, 0) + 1
                total += 1
    return SuppressionCount(total=total, by_kind=by_kind)
