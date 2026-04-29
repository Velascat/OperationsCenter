# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
from pathlib import Path

from operations_center.tuning.metrics import aggregate_family_metrics


def _write_decision(root: Path, run_id: str, *, candidates: list, suppressed: list, dry_run: bool = False) -> None:
    d = root / run_id
    d.mkdir(parents=True)
    artifact = {
        "run_id": run_id,
        "generated_at": "2026-04-04T12:00:00+00:00",
        "source_command": "test",
        "dry_run": dry_run,
        "repo": {"name": "OperationsCenter", "path": "/tmp/repo"},
        "source_insight_run_id": "ins_1",
        "candidates": candidates,
        "suppressed": suppressed,
    }
    (d / "proposal_candidates.json").write_text(json.dumps(artifact))


def _write_proposer(root: Path, run_id: str, decision_run_id: str, *, created: list, skipped: list, failed: list, dry_run: bool = False) -> None:
    d = root / run_id
    d.mkdir(parents=True)
    artifact = {
        "run_id": run_id,
        "generated_at": "2026-04-04T12:00:00+00:00",
        "source_command": "test",
        "dry_run": dry_run,
        "repo": {"name": "OperationsCenter", "path": "/tmp/repo"},
        "source_decision_run_id": decision_run_id,
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }
    (d / "proposal_results.json").write_text(json.dumps(artifact))


def test_aggregates_emitted_and_suppressed(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"

    _write_decision(dec, "dec_1", candidates=[
        {"family": "observation_coverage", "status": "emit"},
        {"family": "test_visibility", "status": "emit"},
    ], suppressed=[
        {"family": "observation_coverage", "reason": "cooldown_active"},
    ])
    _write_proposer(prop, "prop_1", "dec_1", created=[
        {"family": "observation_coverage"},
    ], skipped=[], failed=[])

    metrics, runs, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)

    by_family = {m.family: m for m in metrics}
    obs = by_family["observation_coverage"]
    assert obs.candidates_emitted == 1
    assert obs.candidates_suppressed == 1
    assert obs.candidates_created == 1
    assert obs.suppression_rate == 0.5
    assert obs.create_rate == 1.0

    tv = by_family["test_visibility"]
    assert tv.candidates_emitted == 1
    assert tv.candidates_suppressed == 0
    assert tv.candidates_created == 0
    assert tv.create_rate == 0.0


def test_excludes_dry_run_artifacts(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"

    _write_decision(dec, "dec_dry", candidates=[{"family": "observation_coverage", "status": "emit"}], suppressed=[], dry_run=True)
    _write_decision(dec, "dec_real", candidates=[{"family": "test_visibility", "status": "emit"}], suppressed=[])

    metrics, runs, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)

    families = {m.family for m in metrics}
    assert "observation_coverage" not in families  # dry_run excluded
    assert "test_visibility" in families
    assert runs == 1


def test_returns_empty_when_no_artifacts(tmp_path: Path) -> None:
    metrics, runs, start, end = aggregate_family_metrics(
        decision_root=tmp_path / "missing_decision",
        proposer_root=tmp_path / "missing_proposer",
    )
    assert metrics == []
    assert runs == 0
    assert start is None
    assert end is None


def test_suppression_rate_zero_when_no_suppressions(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[{"family": "dependency_drift", "status": "emit"}], suppressed=[])
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    m = next(m for m in metrics if m.family == "dependency_drift")
    assert m.suppression_rate == 0.0


def test_create_rate_zero_when_none_created(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[{"family": "test_visibility", "status": "emit"}], suppressed=[])
    # no proposer artifact
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    m = next(m for m in metrics if m.family == "test_visibility")
    assert m.create_rate == 0.0


def test_top_suppression_reasons_recorded(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    _write_decision(dec, "dec_1", candidates=[], suppressed=[
        {"family": "observation_coverage", "reason": "cooldown_active"},
        {"family": "observation_coverage", "reason": "cooldown_active"},
        {"family": "observation_coverage", "reason": "quota_exceeded"},
    ])
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p")
    m = next(m for m in metrics if m.family == "observation_coverage")
    assert m.top_suppression_reasons["cooldown_active"] == 2
    assert m.top_suppression_reasons["quota_exceeded"] == 1


def test_window_limits_artifact_count(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    for i in range(10):
        _write_decision(dec, f"dec_{i:02d}", candidates=[{"family": "test_visibility", "status": "emit"}], suppressed=[])

    metrics, runs, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=tmp_path / "p", window=3)
    assert runs == 3


def test_skipped_and_failed_counted(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    prop = tmp_path / "proposer"
    _write_decision(dec, "dec_1", candidates=[
        {"family": "observation_coverage", "status": "emit"},
        {"family": "observation_coverage", "status": "emit"},
    ], suppressed=[])
    _write_proposer(prop, "prop_1", "dec_1",
        created=[{"family": "observation_coverage"}],
        skipped=[{"family": "observation_coverage", "reason": "existing_open_equivalent_task"}],
        failed=[{"family": "observation_coverage", "reason": "plane_create_failed"}],
    )
    metrics, _, _, _ = aggregate_family_metrics(decision_root=dec, proposer_root=prop)
    m = next(m for m in metrics if m.family == "observation_coverage")
    assert m.candidates_skipped == 1
    assert m.candidates_failed == 1
