# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot
from operations_center.proposer.result_models import ProposalResultsArtifact

_FEEDBACK_DIR = Path("state/proposal_feedback")
_PROPOSER_ROOT = Path("tools/report/operations_center/proposer")
_WINDOW_RECORDS = 20  # look at the most recent N feedback records per family
_MIN_RECORDS_FOR_INSIGHT = 5  # need at least this many records before making judgments
_ESCALATION_RATE_THRESHOLD = 0.4  # 40% escalation → high_escalation_rate insight


class ProposalOutcomeDeriver:
    """Derive insights from proposal feedback records written by the reviewer.

    Reads state/proposal_feedback/{task_id}.json records and joins them
    against proposer artifacts to determine the outcome per candidate family.

    Fires:
    - proposal_outcome/high_escalation_rate  — a family is being escalated to humans frequently
    - proposal_outcome/low_acceptance_rate   — a family's proposals are rarely being merged cleanly
    """

    def __init__(self, normalizer: InsightNormalizer) -> None:
        self.normalizer = normalizer

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        feedback_records = self._load_feedback_records()
        if not feedback_records:
            return []

        family_map = self._build_family_map()
        if not family_map:
            return []

        # Annotate each record with its family
        by_family: dict[str, list[dict]] = {}
        for record in feedback_records:
            task_id = record.get("task_id", "")
            family = family_map.get(task_id)
            if not family:
                continue
            by_family.setdefault(family, []).append(record)

        insights: list[DerivedInsight] = []
        observed_at = snapshots[0].observed_at if snapshots else None
        from datetime import UTC, datetime
        now = observed_at or datetime.now(UTC)

        for family, records in by_family.items():
            recent = records[-_WINDOW_RECORDS:]
            if len(recent) < _MIN_RECORDS_FOR_INSIGHT:
                continue

            outcome_counts: Counter[str] = Counter(r.get("outcome", "unknown") for r in recent)
            total = len(recent)
            escalated = outcome_counts.get("escalated", 0)
            merged = outcome_counts.get("merged", 0)
            escalation_rate = escalated / total

            if escalation_rate >= _ESCALATION_RATE_THRESHOLD:
                insights.append(
                    self.normalizer.normalize(
                        kind="proposal_outcome",
                        subject=family,
                        status="high_escalation_rate",
                        key_parts=[family, "high_escalation_rate"],
                        evidence={
                            "family": family,
                            "total_records": total,
                            "escalated": escalated,
                            "merged": merged,
                            "escalation_rate": round(escalation_rate, 2),
                        },
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                )

        return insights

    def _load_feedback_records(self) -> list[dict]:
        if not _FEEDBACK_DIR.exists():
            return []
        records = []
        for path in sorted(_FEEDBACK_DIR.glob("*.json")):
            try:
                records.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return records

    def _build_family_map(self) -> dict[str, str]:
        """Build a map from plane_issue_id (task_id) → family using proposer artifacts."""
        family_map: dict[str, str] = {}
        if not _PROPOSER_ROOT.exists():
            return family_map
        for path in _PROPOSER_ROOT.glob("*/proposal_results.json"):
            try:
                artifact = ProposalResultsArtifact.model_validate_json(path.read_text(encoding="utf-8"))
                for item in artifact.created:
                    if item.plane_issue_id:
                        family_map[item.plane_issue_id] = item.family
            except Exception:
                continue
        return family_map
