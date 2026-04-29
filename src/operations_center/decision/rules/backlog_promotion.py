# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import re
from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:60]


class BacklogPromotionRule:
    """Turns backlog_item insights into proposal candidates.

    One candidate per promotable backlog item. `arch` and `redesign` items are
    never emitted — they are filtered out before reaching this rule.
    """

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []
        for insight in insights:
            if insight.kind != "backlog_item":
                continue

            title = str(insight.evidence.get("title", insight.subject))
            item_type = str(insight.evidence.get("item_type", "feature"))
            description = str(insight.evidence.get("description", ""))
            repo = str(insight.evidence.get("repo", ""))

            summary = f"Backlog item ({item_type}): {title}."
            if description:
                summary += f"\n\n{description}"

            candidates.append(
                CandidateSpec(
                    family="backlog_promotion",
                    subject=title,
                    pattern_key=_slug(title),
                    evidence=dict(insight.evidence),
                    matched_rules=["backlog_promotion"],
                    risk_class="logic",
                    expires_after_runs=7,
                    proposal_outline=ProposalOutline(
                        title_hint=title,
                        summary_hint=summary,
                        labels_hint=["task-kind: improve", f"type: {item_type}", "source: backlog"],
                        source_family="backlog_promotion",
                    ),
                    priority=(0, 0, f"backlog_promotion|{repo}|{_slug(title)}"),
                )
            )
        return candidates
