# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Integration test: Phase 9 → Phase 10 → Phase 11 harvest-replay-regression chain.

Exercises the full chain from a real artifact manifest through fixture harvesting,
slice replay, and mini regression suite execution, asserting the final suite report
status without calling Phase 6 dispatch at any point.
"""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.artifact_index import build_artifact_index, load_artifact_manifest
from operations_center.fixture_harvesting import (
    HarvestProfile,
    HarvestRequest,
    harvest_fixtures,
)
from operations_center.mini_regression import (
    MiniRegressionRunRequest,
    MiniRegressionSuiteDefinition,
    MiniRegressionSuiteEntry,
    load_suite_report,
    run_mini_regression_suite,
)
from operations_center.slice_replay.models import SliceReplayProfile


_RUN_ROOT = "tools/audit/report/representative/ChainTest_run001"


def _make_manifest(tmp_path: Path) -> Path:
    """Write a minimal valid artifact manifest and a real artifact file."""
    run_root = tmp_path / _RUN_ROOT
    run_root.mkdir(parents=True, exist_ok=True)

    artifact_path = run_root / "topic_selection.json"
    artifact_path.write_text(
        json.dumps({"stage": "topic_selection", "result": "ok", "topics": ["A", "B"]}),
        encoding="utf-8",
    )

    manifest_payload = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
        "repo_id": "representative",
        "run_id": "ChainTest_run001",
        "audit_type": "representative",
        "manifest_status": "completed",
        "run_status": "completed",
        "created_at": "2026-04-26T10:00:00Z",
        "updated_at": "2026-04-26T10:01:00Z",
        "finalized_at": "2026-04-26T10:01:00Z",
        "artifact_root": str(tmp_path),
        "run_root": _RUN_ROOT,
        "artifacts": [
            {
                "artifact_id": "videofoundry:representative:TopicSelectionStage:topic_selection",
                "artifact_kind": "stage_report",
                "path": f"{_RUN_ROOT}/topic_selection.json",
                "relative_path": "topic_selection.json",
                "location": "run_root",
                "path_role": "primary",
                "source_stage": "TopicSelectionStage",
                "status": "present",
                "created_at": "2026-04-26T10:00:00Z",
                "updated_at": "2026-04-26T10:00:00Z",
                "size_bytes": 64,
                "content_type": "application/json",
                "checksum": None,
                "consumer_types": ["human_review", "slice_replay"],
                "valid_for": ["current_run_only"],
                "limitations": [],
                "description": "Topic selection output.",
                "metadata": {},
            }
        ],
        "excluded_paths": [],
        "warnings": [],
        "errors": [],
        "limitations": [],
        "metadata": {},
    }

    manifest_path = run_root / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    return manifest_path


def test_harvest_replay_regression_chain_passes(tmp_path: Path):
    """Phase 9 → 10 → 11 end-to-end: harvest a fixture pack, replay it, run regression suite."""
    # Phase 9: Harvest
    manifest_path = _make_manifest(tmp_path)
    manifest = load_artifact_manifest(manifest_path)
    index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

    harvest_request = HarvestRequest(
        index=index,
        harvest_profile=HarvestProfile.FULL_MANIFEST_SNAPSHOT,
    )
    pack, pack_dir = harvest_fixtures(harvest_request, tmp_path / "fixtures")

    assert pack.source_repo_id == "representative"
    assert len(pack.artifacts) >= 1

    # Phase 10: Slice replay via Phase 11 suite
    suite = MiniRegressionSuiteDefinition(
        suite_id="chain_integration_suite",
        name="Chain Integration Suite",
        entries=[
            MiniRegressionSuiteEntry(
                entry_id="chain_integrity",
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

    # Phase 11: Run regression suite
    suite_report = run_mini_regression_suite(run_request)

    assert suite_report.status in ("passed", "partial"), (
        f"Expected passed/partial, got {suite_report.status}. "
        f"Entry results: {[(r.entry_id, r.status, r.error) for r in suite_report.entry_results]}"
    )
    assert suite_report.summary.total_entries == 1
    assert isinstance(suite_report.limitations, list)

    # Report must have been written to disk
    report_path = (
        tmp_path / "suite_out"
        / suite.suite_id
        / suite_report.suite_run_id
        / "suite_report.json"
    )
    assert report_path.exists(), f"Suite report not written to {report_path}"

    # Round-trip load
    loaded = load_suite_report(report_path)
    assert loaded.suite_run_id == suite_report.suite_run_id
    assert loaded.status == suite_report.status


def test_chain_no_dispatch_imported():
    """Phase 9→10→11 chain must never import Phase 6 dispatch."""
    import ast
    from pathlib import Path as _Path

    packages = [
        "fixture_harvesting",
        "slice_replay",
        "mini_regression",
    ]
    src = _Path(__file__).parents[2] / "src" / "operations_center"
    for package in packages:
        pkg_dir = src / package
        for py_file in pkg_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "audit_dispatch" not in node.module, (
                        f"{package}/{py_file.name} imports audit_dispatch — violates chain isolation"
                    )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "audit_dispatch" not in alias.name, (
                            f"{package}/{py_file.name} imports audit_dispatch — violates chain isolation"
                        )
