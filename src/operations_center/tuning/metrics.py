from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from operations_center.tuning.models import FamilyMetrics

# TODO (Phase 6 — per-family confidence threshold) [deferred, reviewed 2026-04-07]
# ConfidenceCalibrationStore exists in operations_center.tuning.calibration with
# record/calibration_for/report, wired into feedback/tuning/worker entrypoints.
# Remaining: make DecisionPolicyConfig.min_confidence per-family — derive from
# calibration_for() instead of the global scalar default once sufficient data exists.
# Unlock condition: ≥3 months of data and ≥20 feedback records per family.
# See docs/design/roadmap.md §Phase 6.

_MIN_SAMPLE_RUNS = 1  # only exclude runs, not families; callers apply their own floor


def aggregate_family_metrics(
    *,
    decision_root: Path,
    proposer_root: Path,
    feedback_root: Path | None = None,
    window: int = 20,
) -> tuple[list[FamilyMetrics], int, datetime | None, datetime | None]:
    """Aggregate per-family behavior metrics from retained decision and proposer artifacts.

    Returns: (family_metrics, run_count, window_start, window_end)
    Excludes dry_run artifacts.
    """
    decision_dirs = _sorted_artifact_dirs(decision_root)[:window]
    if not decision_dirs:
        return [], 0, None, None

    # Load decision artifacts
    decision_artifacts: list[dict[str, object]] = []
    for d in decision_dirs:
        path = d / "proposal_candidates.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if data.get("dry_run"):
            continue
        decision_artifacts.append(data)

    if not decision_artifacts:
        return [], 0, None, None

    # Index proposer artifacts by source_decision_run_id
    proposer_by_decision: dict[str, dict[str, object]] = {}
    if proposer_root.exists():
        for d in _sorted_artifact_dirs(proposer_root):
            path = d / "proposal_results.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            if data.get("dry_run"):
                continue
            src = str(data.get("source_decision_run_id", ""))
            if src:
                proposer_by_decision[src] = data

    # Build plane_issue_id → family map from proposer artifacts
    issue_to_family: dict[str, str] = {}
    if proposer_root.exists():
        for d in _sorted_artifact_dirs(proposer_root):
            path = d / "proposal_results.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
            except Exception:
                continue
            for item in _iter_list(data, "created"):
                issue_id = str(item.get("plane_issue_id", ""))
                family = str(item.get("family", ""))
                if issue_id and family:
                    issue_to_family[issue_id] = family

    # Load proposal feedback records: state/proposal_feedback/*.json
    merged_by_family: Counter[str] = Counter()
    escalated_by_family: Counter[str] = Counter()
    _feedback_root = feedback_root or Path("state/proposal_feedback")
    if _feedback_root.exists():
        for feedback_file in _feedback_root.glob("*.json"):
            try:
                record = json.loads(feedback_file.read_text())
            except Exception:
                continue
            task_id = str(record.get("task_id", ""))
            outcome = str(record.get("outcome", ""))
            family = issue_to_family.get(task_id, "")
            if not family:
                # try plane_issue_id field directly
                family = issue_to_family.get(str(record.get("plane_issue_id", "")), "")
            if not family:
                continue
            if outcome == "merged":
                merged_by_family[family] += 1
            elif outcome == "escalated":
                escalated_by_family[family] += 1

    # Aggregate counts per family
    emitted: Counter[str] = Counter()
    suppressed: Counter[str] = Counter()
    suppression_reasons: dict[str, Counter[str]] = {}
    created: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    failed: Counter[str] = Counter()

    timestamps: list[datetime] = []

    for artifact in decision_artifacts:
        ts_raw = artifact.get("generated_at")
        if ts_raw:
            try:
                timestamps.append(datetime.fromisoformat(str(ts_raw)))
            except ValueError:
                pass

        run_id = str(artifact.get("run_id", ""))

        for candidate in _iter_list(artifact, "candidates"):
            family = str(candidate.get("family", ""))
            if family:
                emitted[family] += 1

        for sup in _iter_list(artifact, "suppressed"):
            family = str(sup.get("family", ""))
            reason = str(sup.get("reason", "unknown"))
            if family:
                suppressed[family] += 1
                suppression_reasons.setdefault(family, Counter())[reason] += 1

        prop = proposer_by_decision.get(run_id)
        if prop:
            for item in _iter_list(prop, "created"):
                family = str(item.get("family", ""))
                if family:
                    created[family] += 1
            for item in _iter_list(prop, "skipped"):
                family = str(item.get("family", ""))
                if family:
                    skipped[family] += 1
            for item in _iter_list(prop, "failed"):
                family = str(item.get("family", ""))
                if family:
                    failed[family] += 1

    all_families = set(emitted) | set(suppressed) | set(created) | set(skipped) | set(failed)
    sample_runs = len(decision_artifacts)

    metrics: list[FamilyMetrics] = []
    for family in sorted(all_families):
        e = emitted[family]
        s = suppressed[family]
        c = created[family]
        sk = skipped[family]
        fa = failed[family]
        total_seen = e + s
        suppression_rate = s / total_seen if total_seen > 0 else 0.0
        create_rate = c / e if e > 0 else 0.0
        no_creation_rate = (e - c) / e if e > 0 else 0.0
        merged = merged_by_family[family]
        escalated = escalated_by_family[family]
        feedback_total = merged + escalated
        acceptance_rate = merged / feedback_total if feedback_total > 0 else 0.0

        metrics.append(
            FamilyMetrics(
                family=family,
                sample_runs=sample_runs,
                candidates_emitted=e,
                candidates_suppressed=s,
                candidates_created=c,
                candidates_skipped=sk,
                candidates_failed=fa,
                suppression_rate=round(suppression_rate, 3),
                create_rate=round(create_rate, 3),
                no_creation_rate=round(no_creation_rate, 3),
                top_suppression_reasons=dict(
                    suppression_reasons.get(family, Counter()).most_common(5)
                ),
                proposals_merged=merged,
                proposals_escalated=escalated,
                acceptance_rate=round(acceptance_rate, 3),
            )
        )

    window_start = min(timestamps) if timestamps else None
    window_end = max(timestamps) if timestamps else None
    return metrics, sample_runs, window_start, window_end


def _sorted_artifact_dirs(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted([d for d in root.iterdir() if d.is_dir()], reverse=True)


def _iter_list(data: dict[str, object], key: str):  # type: ignore[return]
    val = data.get(key, [])
    if isinstance(val, list):
        for item in val:
            if isinstance(item, dict):
                yield item
