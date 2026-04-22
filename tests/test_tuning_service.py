from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from control_plane.tuning.applier import TuningApplier, load_tuning_config
from control_plane.tuning.artifact_writer import TuningArtifactWriter
from control_plane.tuning.guardrails import TuningGuardrails
from control_plane.tuning.loader import TuningArtifactLoader
from control_plane.tuning.models import TuningConfig
from control_plane.tuning.service import TuningContext, TuningRegulatorService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_decision(root: Path, run_id: str, candidates: list, suppressed: list) -> None:
    d = root / run_id
    d.mkdir(parents=True)
    (d / "proposal_candidates.json").write_text(json.dumps({
        "run_id": run_id,
        "generated_at": "2026-04-04T12:00:00+00:00",
        "source_command": "test",
        "dry_run": False,
        "repo": {"name": "r", "path": "/tmp"},
        "source_insight_run_id": "ins_1",
        "candidates": candidates,
        "suppressed": suppressed,
    }))


def _write_proposer(root: Path, run_id: str, decision_run_id: str, created: list) -> None:
    d = root / run_id
    d.mkdir(parents=True)
    (d / "proposal_results.json").write_text(json.dumps({
        "run_id": run_id,
        "generated_at": "2026-04-04T12:00:00+00:00",
        "source_command": "test",
        "dry_run": False,
        "repo": {"name": "r", "path": "/tmp"},
        "source_decision_run_id": decision_run_id,
        "created": created, "skipped": [], "failed": [],
    }))


def _make_context(
    tmp_path: Path,
    *,
    auto_apply: bool = False,
    decision_root: Path | None = None,
    proposer_root: Path | None = None,
) -> TuningContext:
    now = datetime(2026, 4, 4, 12, tzinfo=UTC)
    return TuningContext(
        run_id="tun_test",
        generated_at=now,
        source_command="test",
        decision_root=decision_root or tmp_path / "decision",
        proposer_root=proposer_root or tmp_path / "proposer",
        auto_apply=auto_apply,
        window=20,
        dry_run=True,  # don't write to disk unless we explicitly test writing
    )


def _make_service(tmp_path: Path, *, tuning_config_path: Path | None = None) -> TuningRegulatorService:
    config_path = tuning_config_path or tmp_path / "autonomy_tuning.json"
    return TuningRegulatorService(
        guardrails=TuningGuardrails(max_changes_per_day=2, family_cooldown_hours=48, min_sample_for_apply=5),
        applier=TuningApplier(config_path=config_path),
        loader=TuningArtifactLoader(tuning_root=tmp_path / "tuning"),
        artifact_writer=TuningArtifactWriter(tuning_root=tmp_path / "tuning"),
    )


# ---------------------------------------------------------------------------
# Recommendation-only mode
# ---------------------------------------------------------------------------

def test_recommendation_only_produces_no_config_mutation(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    config_path = tmp_path / "autonomy_tuning.json"

    # Enough data to trigger over-suppressed recommendation
    for i in range(10):
        _write_decision(dec, f"dec_{i:02d}", candidates=[], suppressed=[
            {"family": "observation_coverage", "reason": "cooldown_active"},
        ])

    svc = _make_service(tmp_path, tuning_config_path=config_path)
    ctx = _make_context(tmp_path, auto_apply=False, decision_root=dec)
    artifact, _ = svc.run(ctx)

    assert not config_path.exists(), "recommendation-only must not write config"
    assert artifact.auto_apply is False
    assert any(r.action == "loosen_threshold" for r in artifact.recommendations)
    assert artifact.changes_applied == []


def test_recommendation_only_retains_recommendations(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    for i in range(6):
        _write_decision(dec, f"dec_{i}", candidates=[{"family": "test_visibility", "status": "emit"}], suppressed=[])

    svc = _make_service(tmp_path)
    ctx = _make_context(tmp_path, auto_apply=False, decision_root=dec)
    artifact, _ = svc.run(ctx)

    assert any(r.family == "test_visibility" for r in artifact.recommendations)


def test_returns_empty_artifact_when_no_artifacts(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    ctx = _make_context(tmp_path, auto_apply=False)
    artifact, _ = svc.run(ctx)

    assert artifact.family_metrics == []
    assert artifact.recommendations == []
    assert artifact.window_runs == 0


# ---------------------------------------------------------------------------
# Requested auto-apply is retained as skipped review work
# ---------------------------------------------------------------------------

def test_requested_auto_apply_is_recorded_as_skipped_review_work(tmp_path: Path) -> None:
    dec = tmp_path / "decision"
    config_path = tmp_path / "autonomy_tuning.json"

    # 10 suppressed, 1 emitted → 91% suppression rate → loosen_threshold
    for i in range(10):
        _write_decision(dec, f"dec_{i:02d}", candidates=[], suppressed=[
            {"family": "observation_coverage", "reason": "cooldown_active"},
        ])
    _write_decision(dec, "dec_10", candidates=[{"family": "observation_coverage", "status": "emit"}], suppressed=[])

    svc = _make_service(tmp_path, tuning_config_path=config_path)
    now = datetime(2026, 4, 4, 12, tzinfo=UTC)
    ctx = TuningContext(
        run_id="tun_test",
        generated_at=now,
        source_command="test",
        decision_root=dec,
        proposer_root=tmp_path / "proposer",
        auto_apply=True,
        window=20,
        dry_run=True,
    )
    artifact, _ = svc.run(ctx)

    assert artifact.auto_apply is False
    assert artifact.changes_applied == []
    assert not config_path.exists()
    assert any(s.family == "observation_coverage" for s in artifact.changes_skipped)
    assert all(s.reason == "review_only_runtime" for s in artifact.changes_skipped)


def test_requested_auto_apply_never_mutates_runtime_config(tmp_path: Path) -> None:
    dec = tmp_path / "decision"

    # hotspot_concentration is not in AUTO_APPLY_FAMILIES
    for i in range(10):
        _write_decision(dec, f"dec_{i:02d}", candidates=[], suppressed=[
            {"family": "hotspot_concentration", "reason": "cooldown_active"},
        ])

    svc = _make_service(tmp_path)
    now = datetime(2026, 4, 4, 12, tzinfo=UTC)
    ctx = TuningContext(
        run_id="tun_test", generated_at=now, source_command="test",
        decision_root=dec, proposer_root=tmp_path / "p",
        auto_apply=True, window=20, dry_run=True,
    )
    artifact, _ = svc.run(ctx)

    assert artifact.changes_applied == []
    assert any(s.family == "hotspot_concentration" for s in artifact.changes_skipped)


# ---------------------------------------------------------------------------
# TuningConfig / applier unit
# ---------------------------------------------------------------------------

def test_load_tuning_config_returns_none_when_absent(tmp_path: Path) -> None:
    assert load_tuning_config(tmp_path / "nonexistent.json") is None


def test_applier_writes_and_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    applier = TuningApplier(config_path=path)
    now = datetime(2026, 4, 4, 12, tzinfo=UTC)

    change = applier.apply("observation_coverage", "min_consecutive_runs", "loosen_threshold", "test reason", now)
    assert change is not None
    assert int(change.after) == int(change.before) - 1

    # Re-read
    config = load_tuning_config(path)
    assert config is not None
    assert config.get_int("observation_coverage", "min_consecutive_runs", 999) == int(change.after)


def test_applier_skips_change_outside_range(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    # Manually set to 1 (already at minimum)
    cfg = TuningConfig(
        updated_at=datetime(2026, 4, 4, 12, tzinfo=UTC),
        overrides={"observation_coverage": {"min_consecutive_runs": 1}},
    )
    path.write_text(cfg.model_dump_json())
    applier = TuningApplier(config_path=path)
    change = applier.apply("observation_coverage", "min_consecutive_runs", "loosen_threshold", "test", datetime(2026, 4, 4, 12, tzinfo=UTC))
    assert change is None


# ---------------------------------------------------------------------------
# Artifact writer / loader round-trip
# ---------------------------------------------------------------------------

def test_artifact_writer_writes_all_four_files(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path / "tuning")
    from control_plane.tuning.models import TuningRunArtifact
    artifact = TuningRunArtifact(
        run_id="tun_roundtrip",
        generated_at=datetime(2026, 4, 4, 12, tzinfo=UTC),
        source_command="test",
        window_runs=5,
    )
    paths = writer.write(artifact)
    assert len(paths) == 4
    for p in paths:
        assert Path(p).exists()


def test_loader_reads_written_artifact(tmp_path: Path) -> None:
    writer = TuningArtifactWriter(tuning_root=tmp_path / "tuning")
    from control_plane.tuning.models import TuningRunArtifact
    artifact = TuningRunArtifact(
        run_id="tun_load_test",
        generated_at=datetime(2026, 4, 4, 12, tzinfo=UTC),
        source_command="test",
        window_runs=7,
    )
    writer.write(artifact)

    loader = TuningArtifactLoader(tuning_root=tmp_path / "tuning")
    loaded = loader.load_recent(limit=5)
    assert len(loaded) == 1
    assert loaded[0].run_id == "tun_load_test"
    assert loaded[0].window_runs == 7
