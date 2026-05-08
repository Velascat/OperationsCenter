# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Shared fixtures for mini_regression tests.

Suite definitions are built from fixture packs created via Phase 9 harvesting.
No ExampleManagedRepo audits run. No managed repo code is imported.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
)
from operations_center.slice_replay.models import SliceReplayProfile

_RUN_ROOT = "tools/audit/report/representative/Bucket_run999"


# ---------------------------------------------------------------------------
# Manifest / index helpers (duplicated locally — no cross-test imports)
# ---------------------------------------------------------------------------

def _base_entry(
    artifact_id: str = "example_managed_repo:audit_type_1:TopicSelectionStage:topic_selection",
) -> dict:
    return {
        "artifact_id": artifact_id,
        "artifact_kind": "stage_report",
        "path": f"{_RUN_ROOT}/topic_selection.json",
        "relative_path": "topic_selection.json",
        "location": "run_root",
        "path_role": "primary",
        "source_stage": "TopicSelectionStage",
        "status": "present",
        "created_at": "2026-04-26T12:00:00Z",
        "updated_at": "2026-04-26T12:00:00Z",
        "size_bytes": 512,
        "content_type": "application/json",
        "checksum": None,
        "consumer_types": ["human_review"],
        "valid_for": ["current_run_only"],
        "limitations": [],
        "description": "Topic selection output.",
        "metadata": {},
    }


def _missing_entry(
    artifact_id: str = "example_managed_repo:audit_type_1:VoiceOverStage:asr",
) -> dict:
    return {
        "artifact_id": artifact_id,
        "artifact_kind": "alignment_artifact",
        "path": f"{_RUN_ROOT}/asr.jsonl",
        "relative_path": "asr.jsonl",
        "location": "run_root",
        "path_role": "detail",
        "source_stage": "VoiceOverStage",
        "status": "missing",
        "created_at": None,
        "updated_at": None,
        "size_bytes": None,
        "content_type": "application/x-ndjson",
        "checksum": None,
        "consumer_types": ["slice_replay"],
        "valid_for": ["current_run_only"],
        "limitations": ["partial_run"],
        "description": "ASR observations — not produced.",
        "metadata": {},
    }


def _make_manifest_payload(
    *,
    run_status: str = "completed",
    manifest_status: str = "completed",
    artifacts: list[dict] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    limitations: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "example_managed_repo",
        "repo_id": "audit_type_1",
        "run_id": "Bucket_run999",
        "audit_type": "example_managed_repo_audit",
        "manifest_status": manifest_status,
        "run_status": run_status,
        "created_at": "2026-04-26T12:00:00Z",
        "updated_at": "2026-04-26T12:01:00Z",
        "finalized_at": "2026-04-26T12:01:00Z",
        "artifact_root": "../ExampleManagedRepo",
        "run_root": _RUN_ROOT,
        "artifacts": artifacts if artifacts is not None else [_base_entry()],
        "excluded_paths": [],
        "warnings": warnings if warnings is not None else [],
        "errors": errors if errors is not None else [],
        "limitations": limitations if limitations is not None else [],
        "metadata": {},
    }


def _write_manifest(tmp_path: Path, payload: dict) -> Path:
    run_root = payload["run_root"]
    bucket_dir = tmp_path / run_root
    bucket_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bucket_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    return manifest_path


def _build_index(tmp_path: Path, payload: dict, *, repo_root: Path | None = None):
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path, repo_root=repo_root)


def _make_fixture_pack(index, profile: HarvestProfile, output_dir: Path, **kwargs):
    request = HarvestRequest(index=index, harvest_profile=profile, **kwargs)
    return harvest_fixtures(request, output_dir)


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def good_pack(tmp_path: Path):
    """Fixture pack with one copied JSON artifact."""
    payload = _make_manifest_payload(artifacts=[_base_entry()])
    index = _build_index(tmp_path, payload, repo_root=tmp_path)

    artifact_path = tmp_path / _RUN_ROOT / "topic_selection.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps({"stage": "topic_selection", "result": "ok"}), encoding="utf-8")

    manifest_path = tmp_path / _RUN_ROOT / "artifact_manifest.json"
    manifest = load_artifact_manifest(manifest_path)
    index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

    pack, pack_dir = _make_fixture_pack(
        index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path / "fixtures"
    )
    return pack, pack_dir


@pytest.fixture()
def failure_pack(tmp_path: Path):
    """Fixture pack from a partial run with missing artifacts."""
    payload = _make_manifest_payload(
        run_status="interrupted",
        manifest_status="partial",
        artifacts=[_missing_entry()],
        warnings=["Run interrupted."],
        errors=["SIGTERM"],
        limitations=["partial_run"],
    )
    index = _build_index(tmp_path, payload)
    pack, pack_dir = _make_fixture_pack(
        index, HarvestProfile.MINIMAL_FAILURE, tmp_path / "fixtures"
    )
    return pack, pack_dir


@pytest.fixture()
def simple_suite(good_pack) -> MiniRegressionSuiteDefinition:
    """Suite with one required fixture_integrity entry."""
    _, pack_dir = good_pack
    return MiniRegressionSuiteDefinition(
        suite_id="test_suite",
        name="Test Suite",
        entries=[
            MiniRegressionSuiteEntry(
                entry_id="entry_integrity",
                fixture_pack_path=str(pack_dir),
                replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
            )
        ],
    )


@pytest.fixture()
def mixed_suite(good_pack, failure_pack) -> MiniRegressionSuiteDefinition:
    """Suite with required + optional entries."""
    _, good_dir = good_pack
    _, fail_dir = failure_pack
    return MiniRegressionSuiteDefinition(
        suite_id="mixed_suite",
        name="Mixed Suite",
        entries=[
            MiniRegressionSuiteEntry(
                entry_id="entry_required",
                fixture_pack_path=str(good_dir),
                replay_profile=SliceReplayProfile.FIXTURE_INTEGRITY,
                required=True,
            ),
            MiniRegressionSuiteEntry(
                entry_id="entry_optional",
                fixture_pack_path=str(fail_dir),
                replay_profile=SliceReplayProfile.FAILURE_SLICE,
                required=False,
            ),
        ],
    )


@pytest.fixture()
def run_request(simple_suite, tmp_path) -> MiniRegressionRunRequest:
    return MiniRegressionRunRequest(
        suite_definition=simple_suite,
        output_dir=tmp_path / "suite_output",
    )
