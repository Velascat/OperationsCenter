# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Promote autonomy-created Plane tasks from Backlog → Ready for AI.

When the operator raises a family's autonomy tier from 1 to 2 (via
`autonomy-tiers set`), tasks already sitting in Backlog for that family will
not move automatically — they were created before the tier change. This
service finds those tasks and promotes them.

Promotion criteria (all must be true):
- Task is in "Backlog" state.
- Task has label "source: autonomy".
- Task body contains "source_family: <family>" in the Provenance block.
- Current effective tier for that family is >= 2 (from autonomy_tiers.json).

Safe by default: dry_run=True prints what would happen without touching Plane.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


_SOURCE_FAMILY_RE = re.compile(r"^source_family:\s*(\S+)", re.MULTILINE)
_AUTONOMY_TIER_RE = re.compile(r"^autonomy_tier:\s*(\d+)", re.MULTILINE)


def _parse_source_family(description: str) -> str | None:
    m = _SOURCE_FAMILY_RE.search(description)
    return m.group(1).strip() if m else None


def _family_from_labels(label_names: list[str]) -> str | None:
    """Fallback for tasks whose source_family is in labels rather than description."""
    for name in label_names:
        lower = name.strip().lower()
        if lower.startswith("source-family:"):
            return lower.split(":", 1)[1].strip()
    return None


def _parse_recorded_tier(description: str) -> int | None:
    m = _AUTONOMY_TIER_RE.search(description)
    return int(m.group(1)) if m else None


def _issue_state_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", ""))
    return str(state or "")


def _issue_label_names(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels", [])
    names: list[str] = []
    for label in labels:
        if isinstance(label, dict):
            names.append(str(label.get("name", "")))
        elif isinstance(label, str):
            names.append(label)
    return names


class PlaneClientProtocol(Protocol):
    def list_issues(self) -> list[dict[str, Any]]: ...
    def transition_issue(self, task_id: str, state: str) -> None: ...


class TiersConfigProtocol(Protocol):
    def get_tier(self, family: str) -> int: ...


@dataclass
class PromotedTask:
    task_id: str
    title: str
    family: str
    current_tier: int
    recorded_tier: int | None


@dataclass
class SkippedTask:
    task_id: str
    title: str
    reason: str
    family: str | None = None
    current_tier: int | None = None


@dataclass
class BacklogPromoteResult:
    generated_at: datetime
    dry_run: bool
    promoted: list[PromotedTask] = field(default_factory=list)
    skipped: list[SkippedTask] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def promote_count(self) -> int:
        return len(self.promoted)


class BacklogPromoterService:
    """Promote autonomy-created Backlog tasks whose family tier is now >= 2."""

    def __init__(
        self,
        *,
        plane_client: PlaneClientProtocol,
        get_tier: Any,  # callable(family: str) -> int
        dry_run: bool = True,
    ) -> None:
        self._client = plane_client
        self._get_tier = get_tier
        self.dry_run = dry_run

    def promote(
        self,
        *,
        family_filter: str | None = None,
        issues: list[dict[str, Any]] | None = None,
    ) -> BacklogPromoteResult:
        result = BacklogPromoteResult(
            generated_at=datetime.now(UTC),
            dry_run=self.dry_run,
        )

        if issues is None:
            try:
                issues = self._client.list_issues()
            except Exception as exc:
                result.errors.append(f"Failed to list Plane issues: {exc}")
                return result

        for issue in issues:
            task_id = str(issue.get("id", ""))
            title = str(issue.get("name", "Untitled"))

            # Must be in Backlog
            if _issue_state_name(issue).strip().lower() != "backlog":
                continue

            # Must have source: autonomy label
            label_names = [n.strip().lower() for n in _issue_label_names(issue)]
            if "source: autonomy" not in label_names:
                continue

            description = str(issue.get("description") or issue.get("description_stripped") or "")
            family = _parse_source_family(description) or _family_from_labels(_issue_label_names(issue))

            if family is None:
                result.skipped.append(SkippedTask(
                    task_id=task_id,
                    title=title,
                    reason="no_source_family_in_provenance",
                ))
                continue

            if family_filter is not None and family != family_filter:
                continue

            current_tier = self._get_tier(family)

            if current_tier < 2:
                result.skipped.append(SkippedTask(
                    task_id=task_id,
                    title=title,
                    family=family,
                    current_tier=current_tier,
                    reason="tier_below_2",
                ))
                continue

            recorded_tier = _parse_recorded_tier(description)

            if not self.dry_run:
                try:
                    self._client.transition_issue(task_id, "Ready for AI")
                except Exception as exc:
                    result.errors.append(f"Failed to promote {task_id} ({title}): {exc}")
                    continue

            result.promoted.append(PromotedTask(
                task_id=task_id,
                title=title,
                family=family,
                current_tier=current_tier,
                recorded_tier=recorded_tier,
            ))

        return result
