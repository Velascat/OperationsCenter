# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import re
from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]


class ArchPromotionRule:
    """Turns arch_backlog_item insights into arch_promotion candidates.

    arch_schedule_blocked insights are intentionally ignored here —
    they exist only for operator visibility, not for task creation.
    """

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "arch_backlog_item":
                continue

            title = str(insight.evidence.get("title", insight.subject))
            item_type = str(insight.evidence.get("item_type", "arch"))
            description = str(insight.evidence.get("description", ""))
            repo = str(insight.evidence.get("repo", ""))

            summary = (
                f"Arch-class backlog item ({item_type}): {title}.\n\n"
                "This item was held until execution health metrics confirmed the "
                "codebase is stable (low no-op rate, zero validation failures, "
                "tuning loop showing 'keep' across all default families)."
            )
            if description:
                summary += f"\n\n{description}"

            candidates.append(
                CandidateSpec(
                    family="arch_promotion",
                    subject=title,
                    pattern_key=_slug(title),
                    evidence=dict(insight.evidence),
                    matched_rules=["arch_promotion"],
                    risk_class="arch",
                    expires_after_runs=10,
                    proposal_outline=ProposalOutline(
                        title_hint=title,
                        summary_hint=summary,
                        labels_hint=["task-kind: improve", f"type: {item_type}", "source: backlog"],
                        source_family="arch_promotion",
                    ),
                    priority=(0, 0, f"arch_promotion|{repo}|{_slug(title)}"),
                )
            )
        return candidates
