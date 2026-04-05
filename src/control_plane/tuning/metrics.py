from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from control_plane.tuning.models import FamilyMetrics

_MIN_SAMPLE_RUNS = 1  # only exclude runs, not families; callers apply their own floor


def aggregate_family_metrics(
    *,
    decision_root: Path,
    proposer_root: Path,
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
