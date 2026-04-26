"""Shared fixtures for fixture_harvesting tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from operations_center.artifact_index import build_artifact_index, load_artifact_manifest

_RUN_ROOT = "tools/audit/report/representative/Bucket_run999"


# ---------------------------------------------------------------------------
# Manifest entry builders
# ---------------------------------------------------------------------------

def _base_entry(
    artifact_id: str = "videofoundry:representative:TopicSelectionStage:topic_selection",
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
    artifact_id: str = "videofoundry:representative:VoiceOverStage:asr",
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


def _singleton_entry() -> dict:
    return {
        "artifact_id": "videofoundry:repo_singleton:architecture_invariants:latest",
        "artifact_kind": "architecture_invariant",
        "path": "tools/audit/report/architecture_invariants/latest.json",
        "relative_path": None,
        "location": "repo_singleton",
        "path_role": "primary",
        "source_stage": "architecture_invariants",
        "status": "present",
        "created_at": None,
        "updated_at": "2026-04-25T23:00:00Z",
        "size_bytes": None,
        "content_type": "application/json",
        "checksum": None,
        "consumer_types": ["automated_analysis"],
        "valid_for": ["latest_snapshot"],
        "limitations": ["repo_singleton_overwritten"],
        "description": "Architecture invariant scan.",
        "metadata": {},
    }


def _make_manifest_payload(
    *,
    run_status: str = "completed",
    manifest_status: str = "completed",
    artifacts: list[dict] | None = None,
    excluded_paths: list[dict] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    limitations: list[str] | None = None,
    run_id: str = "run999",
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
        "repo_id": "videofoundry",
        "run_id": run_id,
        "audit_type": "representative",
        "manifest_status": manifest_status,
        "run_status": run_status,
        "created_at": "2026-04-26T12:00:00Z",
        "updated_at": "2026-04-26T12:01:00Z",
        "finalized_at": "2026-04-26T12:01:00Z",
        "artifact_root": "../VideoFoundry",
        "run_root": _RUN_ROOT,
        "artifacts": artifacts if artifacts is not None else [_base_entry()],
        "excluded_paths": excluded_paths if excluded_paths is not None else [],
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def completed_index(tmp_path: Path):
    payload = _make_manifest_payload(artifacts=[_base_entry(), _singleton_entry()])
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path)


@pytest.fixture()
def failed_index(tmp_path: Path):
    payload = _make_manifest_payload(
        run_status="interrupted",
        manifest_status="partial",
        artifacts=[_base_entry(), _missing_entry(), _singleton_entry()],
        warnings=["Run was interrupted."],
        errors=["terminated by signal SIGTERM"],
        limitations=["partial_run", "missing_downstream_artifacts"],
    )
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path)


@pytest.fixture()
def empty_index(tmp_path: Path):
    payload = _make_manifest_payload(artifacts=[])
    manifest_path = _write_manifest(tmp_path, payload)
    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path)


@pytest.fixture()
def index_with_real_file(tmp_path: Path):
    """Index where the artifact file actually exists on disk."""
    payload = _make_manifest_payload(artifacts=[_base_entry()])
    manifest_path = _write_manifest(tmp_path, payload)

    artifact_path = tmp_path / _RUN_ROOT / "topic_selection.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(json.dumps({"key": "value", "stage": "topic_selection"}), encoding="utf-8")

    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path, repo_root=tmp_path)


@pytest.fixture()
def index_with_multiple_real_files(tmp_path: Path):
    """Index with two text artifacts that exist on disk."""
    entry1 = _base_entry()
    entry2 = {
        **_base_entry("videofoundry:representative:ScriptGenerationStage:script"),
        "path": f"{_RUN_ROOT}/script.json",
        "relative_path": "script.json",
        "source_stage": "ScriptGenerationStage",
    }
    payload = _make_manifest_payload(artifacts=[entry1, entry2])
    manifest_path = _write_manifest(tmp_path, payload)

    for entry in [entry1, entry2]:
        p = tmp_path / entry["path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"kind": entry["artifact_kind"]}), encoding="utf-8")

    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path, repo_root=tmp_path)
