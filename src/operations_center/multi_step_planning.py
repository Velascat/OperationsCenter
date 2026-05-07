# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Multi-step planning helpers — split complex goals into a chain of subtasks.

Cited by `docs/design/autonomy/autonomy_gaps.md` §8 (Multi-Step Dependency Planning)
and Wave 4 entries.

What this *does*: classifies a goal as multi-step (by title keywords or
explicit label), and constructs a 3-task plan (Analyze → Implement →
Verify) given the parent. Pure functions plus a thin Plane-task creator
the caller invokes.

What this *does NOT do*: enforce dependency ordering at claim-time.
Dep tracking is F5 in flow_audit and is its own feature; for now,
multi-step children carry `depends-on:` labels that will become
honoured once that feature lands.

Invariants:
  • No imports of behavior_calibration
  • No mutation of frozen contracts
  • No routing decisions (caller decides who claims what)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


_MULTI_STEP_TITLE_KEYWORDS = (
    "refactor", "migrate", "redesign", "modernize", "audit",
    "overhaul", "restructure", "rewrite",
)
_MULTI_STEP_LABEL = "plan: multi-step"


@dataclass(frozen=True)
class MultiStepPlan:
    """A 3-task chain derived from a complex parent goal."""
    parent_id: str
    parent_title: str
    steps: tuple[dict, ...]  # each: {step, title, goal, kind, depends_on}


def _is_multi_step_task(title: str | None, labels: list[str] | None) -> bool:
    """Return True when the task looks complex enough to warrant a chain.

    Two triggers:
      • Title contains any of the heuristic keywords (case-insensitive)
      • Explicit label `plan: multi-step` is present
    """
    if labels:
        normalized = [
            (lbl if isinstance(lbl, str) else (lbl.get("name", "") if isinstance(lbl, dict) else "")).strip().lower()
            for lbl in labels
        ]
        if _MULTI_STEP_LABEL in normalized:
            return True
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in _MULTI_STEP_TITLE_KEYWORDS)


def build_multi_step_plan(
    *,
    parent_id: str,
    parent_title: str,
    parent_goal: str,
    repo_key: str,
) -> MultiStepPlan:
    """Construct a 3-step plan from a complex parent goal.

    Steps:
      1. [Analyze] — scope investigation, no code changes (kind=goal but
         goal text limits scope to read-only analysis).
      2. [Implement] — depends on step 1; the actual code change.
      3. [Verify]   — depends on step 2; tests / validation pass.

    Returns a MultiStepPlan; caller persists each step as a Plane task.
    """
    repo_prefix = f"[{repo_key}] " if repo_key else ""
    steps = (
        {
            "step": 1,
            "title": f"[Step 1/3: Analyze] {repo_prefix}{parent_title}"[:80],
            "goal": (
                f"Repo: {repo_key}\n\nAnalyze the scope of: {parent_goal}\n\n"
                "Read-only step. Identify which files / modules need to change "
                "and what the implementation strategy is. Do not modify code."
            ),
            "kind": "goal",
            "depends_on": [],
        },
        {
            "step": 2,
            "title": f"[Step 2/3: Implement] {repo_prefix}{parent_title}"[:80],
            "goal": (
                f"Repo: {repo_key}\n\nImplement: {parent_goal}\n\n"
                "Use the analysis from step 1 (linked via original-task-id). "
                "Make the code changes; tests come in step 3."
            ),
            "kind": "goal",
            "depends_on": [],  # filled in by caller after step 1 is created
        },
        {
            "step": 3,
            "title": f"[Step 3/3: Verify] {repo_prefix}{parent_title}"[:80],
            "goal": (
                f"Repo: {repo_key}\n\nVerify: {parent_goal}\n\n"
                "Run tests / validation against the implementation from step 2. "
                "Surface any regressions as a follow-up goal task."
            ),
            "kind": "test",
            "depends_on": [],  # filled in by caller after step 2 is created
        },
    )
    return MultiStepPlan(
        parent_id=parent_id,
        parent_title=parent_title,
        steps=steps,
    )


def _score_proposal_utility(
    *,
    family_acceptance_rate: float,
    family_recency_hours: float,
    repo_priority: int = 0,
) -> float:
    """Score a proposal candidate for ranking purposes.

    Higher = pick first. Inputs are read-only metadata; no side effects.
    Used by future priority-rescore scans (Wave 6) to order the queue.

    Components:
      • acceptance rate (0.5 weight)        — proven families first
      • recency (0.3 weight, decays)        — fresh signals over stale
      • repo priority (0.2 weight)          — high-priority repos first
    """
    acc = max(0.0, min(1.0, family_acceptance_rate))
    # Recency: 0h = full weight, 168h (1 week) = ~0; clamp to [0, 1]
    rec = max(0.0, min(1.0, 1.0 - (family_recency_hours / 168.0)))
    pri = max(0.0, min(1.0, repo_priority / 10.0))
    return round(acc * 0.5 + rec * 0.3 + pri * 0.2, 3)


def _requeue_as_goal(parent_task: dict, *, reason: str = "step_failed") -> dict:
    """Build a fresh goal-task spec from a failed multi-step partial.

    Does not call Plane — returns the kwargs the caller passes to
    create_issue. Lets a multi-step chain that fails part-way through
    fall back to a single-shot goal that re-tries the whole thing with
    accumulated context.
    """
    parent_title = parent_task.get("name", "Untitled")
    labels = parent_task.get("labels", []) or []
    parent_id = str(parent_task.get("id", ""))
    inherited_sources = []
    for lab in labels:
        name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
        if name.startswith("source:") and name != "source: board_worker":
            inherited_sources.append(name)
    return {
        "name": f"[goal] {parent_title}",
        "description": (
            f"## Goal\n{parent_title}\n\n"
            f"Re-queued from multi-step chain — {reason}. Treat as single-shot.\n\n"
            f"## Provenance\nrequeued-from: {parent_id}\n"
        ),
        "state": "Ready for AI",
        "label_names": [
            "task-kind: goal",
            "source: board_worker",
            *inherited_sources,
            f"original-task-id: {parent_id}",
            f"handoff-reason: {reason}",
        ],
    }
