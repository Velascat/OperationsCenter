# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""LintClusterRule — propose systematic refactor for persistently-dirty files.

Fires on theme/lint_cluster and theme/type_cluster insights from ThemeAggregationDeriver.
Rather than proposing N individual lint_fix tasks for the same file, proposes a single
targeted refactor/cleanup task with the full file context.
"""
from __future__ import annotations

from collections.abc import Sequence

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.insights.models import DerivedInsight


class LintClusterRule:
    """Turns persistent per-file violation themes into structural cleanup candidates."""

    def evaluate(self, insights: Sequence[DerivedInsight]) -> list[CandidateSpec]:
        candidates: list[CandidateSpec] = []

        for insight in insights:
            if insight.kind == "theme/lint_cluster":
                fpath = insight.evidence.get("file", "unknown")
                appearances = insight.evidence.get("snapshot_appearances", 0)
                candidates.append(
                    CandidateSpec(
                        family="lint_cluster",
                        subject="theme",
                        pattern_key=f"lint_cluster_{fpath}",
                        evidence=dict(insight.evidence),
                        matched_rules=["theme_lint_cluster_persistent"],
                        confidence="high",
                        evidence_lines=[
                            f"{fpath} has appeared in top lint violations in {appearances} consecutive snapshots.",
                            "This pattern suggests a structural issue; a targeted cleanup will prevent recurring proposals.",
                        ],
                        risk_class="style",
                        expires_after_runs=2,
                        proposal_outline=ProposalOutline(
                            title_hint=f"[Refactor] Systematic lint cleanup: {fpath}",
                            summary_hint=(
                                f"{fpath} has appeared in the top lint violations across {appearances} consecutive "
                                "observer snapshots. Rather than applying piecemeal fixes, perform a systematic "
                                "cleanup: run `ruff check --fix` on the file, resolve any unfixable violations "
                                "manually, and refactor patterns that cause lint rules to fire repeatedly."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="lint_cluster",
                        ),
                        priority=(1, 6, f"lint_cluster|{fpath}|{appearances}"),
                    )
                )

            elif insight.kind == "theme/type_cluster":
                fpath = insight.evidence.get("file", "unknown")
                appearances = insight.evidence.get("snapshot_appearances", 0)
                candidates.append(
                    CandidateSpec(
                        family="lint_cluster",
                        subject="theme",
                        pattern_key=f"type_cluster_{fpath}",
                        evidence=dict(insight.evidence),
                        matched_rules=["theme_type_cluster_persistent"],
                        confidence="high",
                        evidence_lines=[
                            f"{fpath} has appeared in top type errors in {appearances} consecutive snapshots.",
                            "Systematic type annotation improvement will prevent recurring type_fix proposals for this file.",
                        ],
                        risk_class="quality",
                        expires_after_runs=2,
                        proposal_outline=ProposalOutline(
                            title_hint=f"[Refactor] Systematic type cleanup: {fpath}",
                            summary_hint=(
                                f"{fpath} has appeared in top type errors across {appearances} consecutive "
                                "observer snapshots. Add or correct type annotations systematically across "
                                "the file rather than applying one-off fixes per snapshot cycle."
                            ),
                            labels_hint=["task-kind: improve", "source: proposer"],
                            source_family="lint_cluster",
                        ),
                        priority=(1, 6, f"type_cluster|{fpath}|{appearances}"),
                    )
                )

        return candidates
