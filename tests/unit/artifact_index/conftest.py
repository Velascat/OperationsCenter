# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Shared fixtures for artifact_index tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Raw manifest payload builders
# ---------------------------------------------------------------------------

_BASE_ENTRY = {
    "artifact_id": "example_managed_repo:audit_type_1:TopicSelectionStage:topic_selection",
    "artifact_kind": "stage_report",
    "path": "tools/audit/report/representative/Bucket_run123/topic_selection.json",
    "relative_path": "topic_selection.json",
    "location": "run_root",
    "path_role": "primary",
    "source_stage": "TopicSelectionStage",
    "status": "present",
    "created_at": "2026-04-26T12:00:00Z",
    "updated_at": "2026-04-26T12:00:00Z",
    "size_bytes": 4096,
    "content_type": "application/json",
    "checksum": None,
    "consumer_types": ["human_review", "automated_analysis"],
    "valid_for": ["current_run_only"],
    "limitations": [],
    "description": "Topic selection output.",
    "metadata": {},
}

_SINGLETON_ENTRY = {
    "artifact_id": "example_managed_repo:repo_singleton:architecture_invariants:latest",
    "artifact_kind": "architecture_invariant",
    "path": "tools/audit/report/architecture_invariants/latest.json",
    "relative_path": None,
    "location": "repo_singleton",
    "path_role": "primary",
    "source_stage": "architecture_invariants",
    "status": "present",
    "created_at": None,
    "updated_at": "2026-04-25T23:03:05Z",
    "size_bytes": None,
    "content_type": "application/json",
    "checksum": None,
    "consumer_types": ["automated_analysis"],
    "valid_for": ["latest_snapshot"],
    "limitations": ["repo_singleton_overwritten"],
    "description": "Architecture invariant scan.",
    "metadata": {},
}

_PARTIAL_ENTRY = {
    "artifact_id": "example_managed_repo:audit_type_1:VoiceOverStage:asr",
    "artifact_kind": "alignment_artifact",
    "path": "tools/audit/report/representative/Bucket_run123/asr.jsonl",
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
    manifest_status: str = "completed",
    run_status: str = "completed",
    artifacts: list[dict] | None = None,
    excluded_paths: list[dict] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    limitations: list[str] | None = None,
    run_id: str = "abc123",
    run_root: str = "tools/audit/report/representative/Bucket_run123",
    **extra,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "example_managed_repo",
        "repo_id": "example_managed_repo",
        "run_id": run_id,
        "audit_type": "audit_type_1",
        "manifest_status": manifest_status,
        "run_status": run_status,
        "created_at": "2026-04-26T12:00:00Z",
        "updated_at": "2026-04-26T12:00:00Z",
        "finalized_at": "2026-04-26T12:01:00Z",
        "artifact_root": "../ExampleManagedRepo",
        "run_root": run_root,
        "artifacts": artifacts if artifacts is not None else [dict(_BASE_ENTRY)],
        "excluded_paths": excluded_paths if excluded_paths is not None else [],
        "warnings": warnings if warnings is not None else [],
        "errors": errors if errors is not None else [],
        "limitations": limitations if limitations is not None else [],
        "metadata": {},
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def completed_manifest_payload() -> dict[str, Any]:
    return _make_manifest_payload(
        artifacts=[dict(_BASE_ENTRY), dict(_SINGLETON_ENTRY)],
    )


@pytest.fixture()
def failed_manifest_payload() -> dict[str, Any]:
    return _make_manifest_payload(
        manifest_status="partial",
        run_status="interrupted",
        artifacts=[dict(_BASE_ENTRY), dict(_PARTIAL_ENTRY), dict(_SINGLETON_ENTRY)],
        warnings=["Run was interrupted."],
        errors=["terminated by signal SIGTERM"],
        limitations=["partial_run", "missing_downstream_artifacts"],
    )


@pytest.fixture()
def completed_manifest_file(tmp_path: Path, completed_manifest_payload) -> Path:
    """A temporary artifact_manifest.json for a completed run."""
    # Mimic the real layout: manifest is inside the bucket dir
    run_root = completed_manifest_payload["run_root"]
    bucket_dir = tmp_path / run_root
    bucket_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bucket_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")
    return manifest_path


@pytest.fixture()
def failed_manifest_file(tmp_path: Path, failed_manifest_payload) -> Path:
    """A temporary artifact_manifest.json for a partial/interrupted run."""
    run_root = failed_manifest_payload["run_root"]
    bucket_dir = tmp_path / run_root
    bucket_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bucket_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(failed_manifest_payload), encoding="utf-8")
    return manifest_path


@pytest.fixture()
def example_completed_manifest_path() -> Path:
    """Path to the canonical completed example in the OC repo."""
    here = Path(__file__).resolve()
    return here.parents[3] / "examples" / "audit_contracts" / "completed_artifact_manifest.json"


@pytest.fixture()
def example_failed_manifest_path() -> Path:
    """Path to the canonical failed example in the OC repo."""
    here = Path(__file__).resolve()
    return here.parents[3] / "examples" / "audit_contracts" / "failed_artifact_manifest.json"
