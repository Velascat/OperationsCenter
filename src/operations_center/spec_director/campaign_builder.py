# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
# src/operations_center/spec_director/campaign_builder.py
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from operations_center.spec_director.models import SpecFrontMatter

logger = logging.getLogger(__name__)

_GOAL_PATTERN = re.compile(r"^\d+\.\s+(.+)$", re.MULTILINE)


@dataclass
class ChildTaskSpec:
    title: str
    goal_text: str
    constraints_text: str
    phase: str  # "implement" | "test_campaign" | "improve_campaign"
    spec_coverage_hint: str


class CampaignBuilder:
    def __init__(
        self,
        client: Any,
        project_id: str,
        max_tasks: int = 6,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._max_tasks = max_tasks

    def build(
        self,
        spec_text: str,
        repo_key: str,
        base_branch: str,
    ) -> list[str]:
        """Create Plane tasks for the campaign. Returns list of created task IDs."""
        fm = SpecFrontMatter.from_spec_text(spec_text)
        goals = self._extract_goals(spec_text)
        constraints = self._extract_section(spec_text, "Constraints")

        # Create parent campaign task
        parent_body = self._build_parent_body(fm, spec_text)
        parent = self._client.create_issue(
            name=f"[Campaign] {fm.slug}",
            description=parent_body,
            label_names=["source: spec-campaign", f"campaign-id: {fm.campaign_id}"],
        )
        parent_id = str(parent["id"])
        created_ids = [parent_id]

        child_count = 0
        for idx, goal_text in enumerate(goals):
            if child_count >= self._max_tasks:
                logger.warning(
                    '{"event": "campaign_task_limit_reached", "campaign_id": "%s", "omitted_goals": %d}',
                    fm.campaign_id, len(goals) - idx,
                )
                break
            for phase in fm.phases:
                if child_count >= self._max_tasks:
                    break
                task_id = self._create_child_task(
                    fm=fm,
                    repo_key=repo_key,
                    base_branch=base_branch,
                    goal_text=goal_text,
                    constraints_text=constraints,
                    phase=phase,
                    goal_index=idx + 1,
                )
                created_ids.append(task_id)
                child_count += 1

        logger.info(
            '{"event": "campaign_created", "campaign_id": "%s", "tasks_created": %d}',
            fm.campaign_id, len(created_ids),
        )
        return created_ids

    def _create_child_task(
        self,
        fm: SpecFrontMatter,
        repo_key: str,
        base_branch: str,
        goal_text: str,
        constraints_text: str,
        phase: str,
        goal_index: int,
    ) -> str:
        task_kind = "goal" if phase == "implement" else phase
        state = "Ready for AI" if phase == "implement" else "Backlog"
        depends_note = ""
        if phase == "test_campaign":
            depends_note = "\n- task_phase_note: Promoted after implement task merges"
        elif phase == "improve_campaign":
            depends_note = "\n- task_phase_note: Promoted after test_campaign passes clean"

        body = f"""## Execution
repo: {repo_key}
base_branch: {base_branch}
mode: {task_kind}
spec_campaign_id: {fm.campaign_id}
spec_file: docs/specs/{fm.slug}.md
task_phase: {phase}
spec_coverage_hint: Goal {goal_index}

## Goal
{goal_text.strip()}

## Constraints
{constraints_text.strip()}{depends_note}
"""
        phase_prefix = {"implement": "Impl", "test_campaign": "Test", "improve_campaign": "Improve"}.get(phase, phase)
        title = f"[{phase_prefix}] {goal_text[:60].strip()}"
        labels = [
            f"task-kind: {task_kind}",
            f"repo: {repo_key}",
            "source: spec-campaign",
            f"campaign-id: {fm.campaign_id}",
        ]
        issue = self._client.create_issue(
            name=title,
            description=body,
            label_names=labels,
            state=state,
        )
        return str(issue["id"])

    @staticmethod
    def _extract_goals(spec_text: str) -> list[str]:
        in_goals = False
        goals = []
        for line in spec_text.splitlines():
            if line.strip().lower().startswith("## goals"):
                in_goals = True
                continue
            if in_goals and line.startswith("##"):
                break
            if in_goals:
                m = _GOAL_PATTERN.match(line)
                if m:
                    goals.append(m.group(1).strip())
        return goals or ["Implement the spec as described"]

    @staticmethod
    def _extract_section(spec_text: str, section: str) -> str:
        in_section = False
        lines = []
        for line in spec_text.splitlines():
            if line.strip().lower() == f"## {section.lower()}":
                in_section = True
                continue
            if in_section and line.startswith("##"):
                break
            if in_section:
                lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _build_parent_body(fm: SpecFrontMatter, spec_text: str) -> str:
        return f"""## Campaign
campaign_id: {fm.campaign_id}
spec_file: docs/specs/{fm.slug}.md
status: active

## Summary
Spec-driven campaign. See spec file for full details.

## Spec Preview
{spec_text[:800]}...
"""
