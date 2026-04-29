# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""NoOpLoopDeriver — detects families cycling without acceptance.

When the same proposal family is proposed multiple times in a rolling window but
generates zero accepted outcomes (merges), it indicates the loop is running but
producing no net improvement.  This surfaces the pattern so operators can adjust
thresholds, tiers, or intervene manually.

Emits:
  noop_loop/family_cycling  — a family has been proposed ≥3 times in 30 days
                              with 0 merged outcomes in that period
"""
from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot

_PROPOSAL_ROOT = Path("tools/report/operations_center/proposer")
_FEEDBACK_ROOT = Path("state/proposal_feedback")
_LOOK_BACK_DAYS = 30
_MIN_PROPOSALS_TO_FLAG = 3    # family must have been proposed at least this many times


class NoOpLoopDeriver:
    """Detects families that keep cycling without producing accepted work."""

    def __init__(
        self,
        normalizer: InsightNormalizer,
        *,
        proposer_root: Path = _PROPOSAL_ROOT,
        feedback_root: Path = _FEEDBACK_ROOT,
        look_back_days: int = _LOOK_BACK_DAYS,
        min_proposals: int = _MIN_PROPOSALS_TO_FLAG,
    ) -> None:
        self.normalizer = normalizer
        self.proposer_root = proposer_root
        self.feedback_root = feedback_root
        self.look_back_days = look_back_days
        self.min_proposals = min_proposals

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []

        cutoff = datetime.now(UTC) - timedelta(days=self.look_back_days)

        # --- Count proposals per family in the look-back window ---
        family_proposals: dict[str, int] = defaultdict(int)
        try:
            for artifact_path in sorted(self.proposer_root.glob("proposer_result_*.json")):
                try:
                    data = json.loads(artifact_path.read_text())
                    written_at_str = data.get("generated_at") or data.get("written_at") or ""
                    if written_at_str:
                        written_at = datetime.fromisoformat(written_at_str)
                        if written_at.tzinfo is None:
                            written_at = written_at.replace(tzinfo=UTC)
                        if written_at < cutoff:
                            continue
                    # Each created task in the result counts as one proposal
                    for task in data.get("created_tasks", []):
                        fam = task.get("source_family") or task.get("family") or ""
                        if fam:
                            family_proposals[fam] += 1
                except Exception:
                    continue
        except (OSError, FileNotFoundError):
            pass

        # --- Count acceptances (merged) per family in the same window ---
        family_merges: dict[str, int] = defaultdict(int)
        try:
            for feedback_path in self.feedback_root.glob("*.json"):
                try:
                    data = json.loads(feedback_path.read_text())
                    rec_at_str = data.get("recorded_at") or ""
                    if rec_at_str:
                        rec_at = datetime.fromisoformat(rec_at_str)
                        if rec_at.tzinfo is None:
                            rec_at = rec_at.replace(tzinfo=UTC)
                        if rec_at < cutoff:
                            continue
                    if data.get("outcome") == "merged":
                        # Try to derive family from source_family field
                        fam = data.get("source_family") or data.get("family") or ""
                        if fam:
                            family_merges[fam] += 1
                except Exception:
                    continue
        except (OSError, FileNotFoundError):
            pass

        # --- Emit insights for cycling families ---
        insights: list[DerivedInsight] = []
        now = snapshots[0].observed_at
        for family, proposal_count in family_proposals.items():
            if proposal_count < self.min_proposals:
                continue
            merge_count = family_merges.get(family, 0)
            if merge_count > 0:
                # Has at least one acceptance — not cycling
                continue
            insights.append(
                self.normalizer.normalize(
                    kind="noop_loop/family_cycling",
                    subject="noop_loop",
                    status="present",
                    key_parts=["family_cycling", family],
                    evidence={
                        "family": family,
                        "proposals_in_window": proposal_count,
                        "merges_in_window": merge_count,
                        "look_back_days": self.look_back_days,
                    },
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )

        return insights
