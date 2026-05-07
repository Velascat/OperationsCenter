# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Heuristic quality check for PR description bodies.

Cited by `docs/design/autonomy/autonomy_gaps.md` S9-8 (PR Description Quality Check).
A bot-written PR with an empty or one-line body makes review harder; this
gate gives a small signal when the body is missing, too short, or skips
the conventional sections (## Goal, ## Changes, etc.).

Read-only: returns a structured result. The caller decides what to do
(comment on the PR, mark needs-improvement, etc.). No state writes.
"""
from __future__ import annotations

from dataclasses import dataclass

# Minimum useful body length in characters. Tuned for "more than a one-liner,
# less than a novel". Bot-generated PRs that fall below are usually missing
# context.
_MIN_BODY_CHARS = 80
# Sections we expect in a well-formed bot-written PR.
_RECOMMENDED_SECTIONS = ("## goal", "## changes", "## summary", "## why")


@dataclass(frozen=True)
class PRQualityCheck:
    """Result of a PR description quality check."""
    ok: bool
    score: float                      # 0.0 - 1.0
    reasons: tuple[str, ...]
    body_length: int


def _check_pr_description_quality(body: str | None) -> PRQualityCheck:
    """Score a PR description body. Conservative — only flags clearly thin bodies.

    Components contributing to the score (each worth a fixed weight):
      • body present and ≥ _MIN_BODY_CHARS         (0.40)
      • contains at least one recommended section  (0.30)
      • not just an embedded diff with no prose    (0.30)

    The heuristic is intentionally lenient — a hand-written PR is allowed
    to skip the bot conventions, and a brief but substantive body still
    passes the body-length check.
    """
    if not body:
        return PRQualityCheck(ok=False, score=0.0,
                              reasons=("empty_body",),
                              body_length=0)
    body = body.strip()
    body_length = len(body)
    reasons: list[str] = []
    score = 0.0

    if body_length >= _MIN_BODY_CHARS:
        score += 0.40
    else:
        reasons.append(f"body_too_short({body_length}<{_MIN_BODY_CHARS})")

    body_lower = body.lower()
    if any(s in body_lower for s in _RECOMMENDED_SECTIONS):
        score += 0.30
    else:
        reasons.append("missing_recommended_section")

    # "Just an embedded diff" — the body is dominated by code blocks with
    # no prose lines around them. Naive check: count non-fenced lines.
    in_fence = False
    prose_lines = 0
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence and line and not line.startswith(("#", "*", "-", "|", ">")):
            prose_lines += 1
    if prose_lines >= 2:
        score += 0.30
    else:
        reasons.append("no_prose_explanation")

    ok = score >= 0.50
    return PRQualityCheck(
        ok=ok,
        score=round(score, 2),
        reasons=tuple(reasons),
        body_length=body_length,
    )
