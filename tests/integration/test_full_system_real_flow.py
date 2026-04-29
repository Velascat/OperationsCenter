# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Integration test: full system real flow (no real VideoFoundry subprocess).

Exercises the complete pipeline from a fake VideoFoundry producer through:
  Phase 5  — Producer output (run_status.json + artifact_manifest.json)
  Phase 6  — Governance approval + dispatch (mocked)
  Phase 7  — Artifact index build
  Phase 8  — Behavior calibration (advisory)
  Phase 9  — Fixture harvesting
  Phase 10 — Slice replay (via Phase 11 suite)
  Phase 11 — Mini regression suite execution

No VideoFoundry code is imported. All producer artifacts are written by this test
using the Phase 2 contract schema so OpsCenter can load them through its own
discovery chain.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch


from operations_center.artifact_index import build_artifact_index, load_artifact_manifest
from operations_center.audit_dispatch.models import DispatchStatus, ManagedAuditDispatchResult
from operations_center.audit_governance import (
    AuditGovernanceRequest,
    GovernanceConfig,
    run_governed_audit,
)
from operations_center.audit_toolset.discovery import load_run_status_entrypoint
from operations_center.behavior_calibration import (
    AnalysisProfile,
    BehaviorCalibrationInput,
    analyze_artifacts,
)
from operations_center.fixture_harvesting import (
    HarvestProfile,
    HarvestRequest,
    harvest_fixtures,
)
from operations_center.mini_regression import (
    MiniRegressionRunRequest,
    MiniRegressionSuiteDefinition,
    MiniRegressionSuiteEntry,
    run_mini_regression_suite,
)
from operations_center.slice_replay.models import SliceReplayProfile


_REPO_ID = "videofoundry"
_AUDIT_TYPE = "representative"
_RUN_ID = "FullSystemTest_run001"
_DISPATCH_TARGET = "operations_center.audit_governance.runner.dispatch_managed_audit"


# ---------------------------------------------------------------------------
# Fake producer: write Phase 5 output files exactly per Phase 2 contract
# ---------------------------------------------------------------------------

def _write_fake_producer_outputs(run_dir: Path) -> tuple[Path, Path]:
    """Simulate a completed VideoFoundry audit run.

    Returns (run_status_path, artifact_manifest_path).
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write a real artifact file
    artifact_file = run_dir / "topic_selection.json"
    artifact_file.write_text(
        json.dumps({
            "stage": "TopicSelectionStage",
            "result": "ok",
            "topics": ["AI developments", "Climate tech"],
            "selected": "AI developments",
        }),
        encoding="utf-8",
    )

    # artifact_manifest.json
    manifest = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": _REPO_ID,
        "repo_id": _REPO_ID,
        "run_id": _RUN_ID,
        "audit_type": _AUDIT_TYPE,
        "manifest_status": "completed",
        "run_status": "completed",
        "created_at": "2026-04-26T08:00:00Z",
        "updated_at": "2026-04-26T08:05:00Z",
        "finalized_at": "2026-04-26T08:05:00Z",
        "artifact_root": str(run_dir.parent),
        "run_root": run_dir.name,
        "artifacts": [
            {
                "artifact_id": f"{_REPO_ID}:{_AUDIT_TYPE}:TopicSelectionStage:topic_selection",
                "artifact_kind": "stage_report",
                "path": str(artifact_file),
                "relative_path": artifact_file.name,
                "location": "run_root",
                "path_role": "primary",
                "source_stage": "TopicSelectionStage",
                "status": "present",
                "created_at": "2026-04-26T08:01:00Z",
                "updated_at": "2026-04-26T08:01:00Z",
                "size_bytes": artifact_file.stat().st_size,
                "content_type": "application/json",
                "checksum": None,
                "consumer_types": ["human_review", "slice_replay"],
                "valid_for": ["current_run_only"],
                "limitations": [],
                "description": "Topic selection stage output.",
                "metadata": {},
            }
        ],
        "excluded_paths": [],
        "warnings": [],
        "errors": [],
        "limitations": [],
        "metadata": {},
    }
    manifest_path = run_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # run_status.json — written last, as VideoFoundry finalizer does
    run_status = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": _REPO_ID,
        "run_id": _RUN_ID,
        "repo_id": _REPO_ID,
        "audit_type": _AUDIT_TYPE,
        "status": "completed",
        "current_phase": "finalized",
        "started_at": "2026-04-26T08:00:00Z",
        "completed_at": "2026-04-26T08:05:00Z",
        "artifact_manifest_path": str(manifest_path),
        "error": None,
        "traceback": None,
        "metadata": {},
    }
    status_path = run_dir / "run_status.json"
    status_path.write_text(json.dumps(run_status, indent=2), encoding="utf-8")

    return status_path, manifest_path


def _write_fake_suite_report(output_dir: Path) -> Path:
    """Write a minimal valid suite report so mini_regression_first_policy passes."""
    now = "2026-04-26T08:00:00Z"
    report = {
        "schema_version": "1.0",
        "suite_run_id": "pre_audit_regression_001",
        "suite_id": "pre_audit_suite",
        "suite_name": "Pre-Audit Regression Suite",
        "created_at": now,
        "started_at": now,
        "ended_at": now,
        "status": "passed",
        "entry_results": [],
        "summary": {
            "total_entries": 0,
            "required_entries": 0,
            "optional_entries": 0,
            "passed_entries": 0,
            "failed_entries": 0,
            "error_entries": 0,
            "skipped_entries": 0,
            "required_failures": 0,
            "optional_failures": 0,
        },
        "report_paths": [],
        "limitations": [],
        "metadata": {},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "suite_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def _make_dispatch_result(run_dir: Path, manifest_path: Path) -> ManagedAuditDispatchResult:
    """Dispatch mock return value that includes the producer artifact paths."""
    now = datetime.now(UTC)
    return ManagedAuditDispatchResult(
        repo_id=_REPO_ID,
        audit_type=_AUDIT_TYPE,
        run_id=_RUN_ID,
        status=DispatchStatus.COMPLETED,
        started_at=now,
        ended_at=now,
        duration_seconds=300.0,
        artifact_manifest_path=str(manifest_path),
    )


# ---------------------------------------------------------------------------
# Full system flow test
# ---------------------------------------------------------------------------

def test_full_system_real_flow(tmp_path: Path):
    """
    VideoFoundry fake run → OpsCenter discovery → index → calibration
    → fixture harvest → slice replay → regression suite.

    Each phase asserts its contract before handing off to the next.
    """
    # --- Phase 5: Fake producer writes contract files ---
    run_dir = tmp_path / "runs" / _RUN_ID
    status_path, manifest_path = _write_fake_producer_outputs(run_dir)

    # --- Phase 6: Governance + dispatch (mocked — dispatch already ran) ---
    # Mini regression policy requires a prior suite report for urgency='normal'
    suite_report_path = _write_fake_suite_report(tmp_path / "pre_audit_regression")
    request = AuditGovernanceRequest(
        repo_id=_REPO_ID,
        audit_type=_AUDIT_TYPE,
        requested_by="integration_test",
        requested_reason="full system flow validation",
        urgency="normal",
        related_suite_report_path=str(suite_report_path),
    )
    cfg = GovernanceConfig(
        known_repos=[_REPO_ID],
        known_audit_types={_REPO_ID: [_AUDIT_TYPE]},
        state_dir=tmp_path / "gov_state",
    )
    dispatch_result = _make_dispatch_result(run_dir, manifest_path)
    with patch(_DISPATCH_TARGET, return_value=dispatch_result):
        gov_result = run_governed_audit(
            request,
            governance_config=cfg,
            output_dir=tmp_path / "governance_out",
        )

    assert gov_result.governance_status == "approved_and_dispatched"
    assert gov_result.dispatch_result is not None
    assert gov_result.dispatch_result.run_id == _RUN_ID

    # Governance report must be persisted with governance_status field
    from operations_center.audit_governance import load_governance_report
    gov_report = load_governance_report(Path(gov_result.report_path))
    assert gov_report.governance_status == "approved_and_dispatched"
    assert gov_report.dispatched_run_id == _RUN_ID

    # --- Phase 7: Discovery → artifact index ---
    # OpsCenter discovers artifacts via the path embedded in the dispatch result.
    run_status = load_run_status_entrypoint(status_path)
    assert run_status.status == "completed"
    assert run_status.run_id == _RUN_ID

    discovered_manifest = load_artifact_manifest(Path(run_status.artifact_manifest_path))
    assert discovered_manifest.run_id == _RUN_ID
    assert len(discovered_manifest.artifacts) == 1

    index = build_artifact_index(discovered_manifest, manifest_path, repo_root=run_dir.parent)
    assert len(index.artifacts) == 1
    artifact = index.artifacts[0]
    assert artifact.artifact_kind == "stage_report"
    assert artifact.source_stage == "TopicSelectionStage"

    # --- Phase 8: Behavior calibration (advisory only) ---
    calib_input = BehaviorCalibrationInput(
        repo_id=_REPO_ID,
        run_id=_RUN_ID,
        audit_type=_AUDIT_TYPE,
        artifact_index=index,
        analysis_profile=AnalysisProfile.SUMMARY,
    )
    calib_report = analyze_artifacts(calib_input)
    assert calib_report is not None
    assert calib_report.analysis_profile == AnalysisProfile.SUMMARY
    # Calibration is advisory — even warnings are non-fatal
    assert calib_report.artifact_index_summary.total_artifacts >= 1

    # --- Phase 9: Fixture harvesting ---
    harvest_request = HarvestRequest(
        index=index,
        harvest_profile=HarvestProfile.FULL_MANIFEST_SNAPSHOT,
    )
    fixture_pack, pack_dir = harvest_fixtures(harvest_request, tmp_path / "fixtures")

    assert fixture_pack.source_repo_id == _REPO_ID
    assert fixture_pack.source_run_id == _RUN_ID
    assert len(fixture_pack.artifacts) >= 1

    # --- Phase 10+11: Slice replay + mini regression suite ---
    suite = MiniRegressionSuiteDefinition(
        suite_id="full_system_suite",
        name="Full System Integration Suite",
        entries=[
            MiniRegressionSuiteEntry(
                entry_id="full_system_integrity",
                fixture_pack_path=str(pack_dir),
                replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                required=True,
            ),
        ],
    )
    run_request = MiniRegressionRunRequest(
        suite_definition=suite,
        output_dir=tmp_path / "suite_out",
    )
    suite_report = run_mini_regression_suite(run_request)

    assert suite_report.status in ("passed", "partial"), (
        f"Suite failed: {[(r.entry_id, r.status, r.error) for r in suite_report.entry_results]}"
    )
    assert suite_report.summary.total_entries == 1
    assert isinstance(suite_report.limitations, list)

    # Suite report must be persisted
    report_file = (
        tmp_path / "suite_out"
        / suite.suite_id
        / suite_report.suite_run_id
        / "suite_report.json"
    )
    assert report_file.exists(), f"Suite report not written: {report_file}"


def test_full_system_governance_status_in_report(tmp_path: Path):
    """governance_status must be persisted in the governance_report.json (gap_r2_003)."""
    run_dir = tmp_path / "runs" / _RUN_ID
    _, manifest_path = _write_fake_producer_outputs(run_dir)

    suite_report_path = _write_fake_suite_report(tmp_path / "pre_audit_regression")
    request = AuditGovernanceRequest(
        repo_id=_REPO_ID,
        audit_type=_AUDIT_TYPE,
        requested_by="integration_test",
        requested_reason="status persistence check",
        urgency="normal",
        related_suite_report_path=str(suite_report_path),
    )
    cfg = GovernanceConfig(
        known_repos=[_REPO_ID],
        known_audit_types={_REPO_ID: [_AUDIT_TYPE]},
        state_dir=tmp_path / "gov_state",
    )
    dispatch_result = _make_dispatch_result(run_dir, manifest_path)
    with patch(_DISPATCH_TARGET, return_value=dispatch_result):
        gov_result = run_governed_audit(
            request,
            governance_config=cfg,
            output_dir=tmp_path / "governance_out",
        )

    from operations_center.audit_governance import load_governance_report
    _gov_report = load_governance_report(Path(gov_result.report_path))

    # The persisted JSON must contain governance_status (gap_r2_003 closure check)
    raw = json.loads(Path(gov_result.report_path).read_text(encoding="utf-8"))
    assert "governance_status" in raw, "governance_status must be persisted in governance_report.json"
    assert raw["governance_status"] == "approved_and_dispatched"


def test_full_system_denied_request_leaves_audit_unrun(tmp_path: Path):
    """A denied governance request must not trigger dispatch."""
    request = AuditGovernanceRequest(
        repo_id="unknown_repo",
        audit_type="representative",
        requested_by="integration_test",
        requested_reason="denial test",
        urgency="normal",
    )
    cfg = GovernanceConfig(
        known_repos=[_REPO_ID],
        known_audit_types={_REPO_ID: ["representative"]},
        state_dir=tmp_path / "gov_state",
    )
    dispatch_mock = MagicMock()
    with patch(_DISPATCH_TARGET, dispatch_mock):
        gov_result = run_governed_audit(
            request,
            governance_config=cfg,
            output_dir=tmp_path / "governance_out",
        )

    assert gov_result.governance_status == "denied"
    assert not dispatch_mock.called, "Dispatch must not be called for denied requests"
    assert gov_result.dispatch_result is None


def test_full_system_no_videoFoundry_imports():
    """OpsCenter Phase 5–11 pipeline modules must not import VideoFoundry code."""
    import ast
    from pathlib import Path as _Path

    pipeline_packages = [
        "audit_contracts",
        "audit_toolset",
        "artifact_index",
        "behavior_calibration",
        "fixture_harvesting",
        "slice_replay",
        "mini_regression",
        "audit_governance",
    ]
    _forbidden = ("videofoundry", "tools.audit", "managed_repo")
    src = _Path(__file__).parents[2] / "src" / "operations_center"

    for package in pipeline_packages:
        pkg_dir = src / package
        if not pkg_dir.exists():
            continue
        for py_file in pkg_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif node.module:
                        modules = [node.module]
                    for mod in modules:
                        for forbidden in _forbidden:
                            assert not mod.startswith(forbidden), (
                                f"{package}/{py_file.name} imports forbidden "
                                f"module {mod!r} (matches {forbidden!r})"
                            )
