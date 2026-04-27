"""Integration test: Phase 5 fake-producer contract flow.

Simulates what a VideoFoundry audit would produce (run_status.json +
artifact_manifest.json), then validates that the OpsCenter Phase 6→7
discovery chain can load and index the artifacts without any VideoFoundry
code imports.

This test does NOT call the real VideoFoundry subprocess. It writes the
files that VideoFoundry would produce, exactly matching the Phase 2 contract.
"""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.artifact_index import build_artifact_index, load_artifact_manifest
from operations_center.audit_toolset.discovery import load_run_status_entrypoint


_REPO_ID = "videofoundry"
_AUDIT_TYPE = "representative"
_RUN_ID = "FakeProducer_run001_2026042600000000"


# ---------------------------------------------------------------------------
# Fake producer helpers (simulate VideoFoundry output)
# ---------------------------------------------------------------------------

def _write_run_status(run_dir: Path, manifest_path: Path) -> Path:
    """Write a run_status.json as VideoFoundry would after a successful audit."""
    status = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
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
    path = run_dir / "run_status.json"
    path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return path


def _write_artifact_manifest(run_dir: Path, artifact_file: Path) -> Path:
    """Write an artifact_manifest.json as VideoFoundry would produce."""
    manifest = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
        "repo_id": _REPO_ID,
        "run_id": _RUN_ID,
        "audit_type": _AUDIT_TYPE,
        "manifest_status": "completed",
        "run_status": "completed",
        "created_at": "2026-04-26T08:00:00Z",
        "updated_at": "2026-04-26T08:05:00Z",
        "finalized_at": "2026-04-26T08:05:00Z",
        "artifact_root": str(artifact_file.parent.parent),
        "run_root": str(run_dir.relative_to(artifact_file.parent.parent.parent)),
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
    path = run_dir / "artifact_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fake_producer_run_status_is_valid(tmp_path: Path):
    """run_status.json written by the fake producer must validate against Phase 2 contract."""
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    artifact_file = run_dir / "topic_selection.json"
    artifact_file.write_text(json.dumps({"result": "ok"}), encoding="utf-8")
    manifest_path = _write_artifact_manifest(run_dir, artifact_file)
    status_path = _write_run_status(run_dir, manifest_path)

    run_status = load_run_status_entrypoint(status_path)
    assert run_status.status == "completed"
    assert run_status.run_id == _RUN_ID


def test_fake_producer_manifest_loadable(tmp_path: Path):
    """artifact_manifest.json written by the fake producer must load cleanly via Phase 7."""
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    artifact_file = run_dir / "topic_selection.json"
    artifact_file.write_text(json.dumps({"result": "ok"}), encoding="utf-8")
    manifest_path = _write_artifact_manifest(run_dir, artifact_file)

    manifest = load_artifact_manifest(manifest_path)
    assert manifest.run_id == _RUN_ID
    assert manifest.manifest_status == "completed"
    assert len(manifest.artifacts) == 1


def test_fake_producer_discovery_chain(tmp_path: Path):
    """OpsCenter discovers manifest via run_status.artifact_manifest_path (Phase 3 contract)."""
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    artifact_file = run_dir / "topic_selection.json"
    artifact_file.write_text(json.dumps({"result": "ok"}), encoding="utf-8")
    manifest_path = _write_artifact_manifest(run_dir, artifact_file)
    status_path = _write_run_status(run_dir, manifest_path)

    # Discovery: OpsCenter reads run_status, follows artifact_manifest_path
    run_status = load_run_status_entrypoint(status_path)
    assert run_status.artifact_manifest_path is not None

    discovered_manifest_path = Path(run_status.artifact_manifest_path)
    assert discovered_manifest_path.exists()

    manifest = load_artifact_manifest(discovered_manifest_path)
    assert manifest.run_id == run_status.run_id


def test_fake_producer_artifact_index_built(tmp_path: Path):
    """Phase 7 index built from a fake-producer manifest must resolve artifact paths."""
    run_dir = tmp_path / "run_001"
    run_dir.mkdir()
    artifact_file = run_dir / "topic_selection.json"
    artifact_file.write_text(json.dumps({"result": "ok"}), encoding="utf-8")
    manifest_path = _write_artifact_manifest(run_dir, artifact_file)

    manifest = load_artifact_manifest(manifest_path)
    index = build_artifact_index(manifest, manifest_path)

    assert len(index.artifacts) == 1
    entry = index.artifacts[0]
    assert entry.status == "present"


def test_fake_producer_failed_run_status_loads(tmp_path: Path):
    """A failed run_status.json (no artifact_manifest_path) must also load cleanly."""
    run_dir = tmp_path / "run_failed"
    run_dir.mkdir()
    failed_status = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
        "run_id": _RUN_ID,
        "repo_id": _REPO_ID,
        "audit_type": _AUDIT_TYPE,
        "status": "failed",
        "current_phase": "TopicSelectionStage",
        "started_at": "2026-04-26T08:00:00Z",
        "artifact_manifest_path": None,
        "error": "SIGTERM received",
        "traceback": "Traceback (most recent call last):\n  ...\nKeyboardInterrupt",
        "metadata": {},
    }
    status_path = run_dir / "run_status.json"
    status_path.write_text(json.dumps(failed_status), encoding="utf-8")

    run_status = load_run_status_entrypoint(status_path)
    assert run_status.status == "failed"
    assert run_status.artifact_manifest_path is None
    assert run_status.error == "SIGTERM received"


def test_no_videofoundry_imports_in_discovery_chain():
    """The Phase 6→7 discovery chain must never import VideoFoundry code."""
    import ast
    from pathlib import Path as _Path

    packages = ["audit_contracts", "artifact_index", "audit_dispatch"]
    src = _Path(__file__).parents[2] / "src" / "operations_center"
    for package in packages:
        pkg_dir = src / package
        if not pkg_dir.exists():
            continue
        for py_file in pkg_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("videofoundry"), (
                        f"{package}/{py_file.name} imports videofoundry — forbidden"
                    )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("videofoundry"), (
                            f"{package}/{py_file.name} imports videofoundry — forbidden"
                        )
