# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""CLI tests for operations-center-artifacts commands.

Covers index / list / get / query using typer.testing.CliRunner.
Manifest loading is monkeypatched — no real files on disk are needed
for the happy-path tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from operations_center.entrypoints.artifacts.main import app

_runner = CliRunner()

_LOAD_MANIFEST_TARGET = "operations_center.entrypoints.artifacts.main.load_artifact_manifest"
_BUILD_INDEX_TARGET = "operations_center.entrypoints.artifacts.main.build_artifact_index"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_index(
    repo_id: str = "videofoundry",
    audit_type: str = "representative",
    run_id: str = "run_001",
    artifacts: list | None = None,
) -> MagicMock:
    index = MagicMock()
    index.source.repo_id = repo_id
    index.source.audit_type = audit_type
    index.source.run_id = run_id
    index.manifest_status.value = "finalized"
    index.run_status.value = "completed"
    index.artifacts = artifacts or []
    index.singleton_artifacts = []
    index.excluded_paths = []
    index.limitations = []
    index.warnings = []
    index.errors = []
    return index


def _make_mock_artifact(artifact_id: str = "vf:rep:encode:output") -> MagicMock:
    art = MagicMock()
    art.artifact_id = artifact_id
    art.artifact_kind = "output_video"
    art.location.value = "artifacts_subdir"
    art.path_role.value = "primary"
    art.source_stage = "encode"
    art.status.value = "present"
    art.path = "/tmp/output.mp4"
    art.resolved_path = Path("/tmp/output.mp4")
    art.exists_on_disk = True
    art.content_type = "video/mp4"
    art.size_bytes = 1024
    art.consumer_types = []
    art.valid_for = []
    art.limitations = []
    art.is_repo_singleton = False
    art.is_partial = False
    art.description = "Encoded output video"
    return art


def _make_manifest_file(tmp_path: Path) -> Path:
    p = tmp_path / "artifact_manifest.json"
    p.write_text("{}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# cmd_index
# ---------------------------------------------------------------------------

class TestCmdIndex:
    def test_index_summary_output(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        with patch(_LOAD_MANIFEST_TARGET), patch(_BUILD_INDEX_TARGET, return_value=index):
            out = _runner.invoke(app, ["index", "--manifest", str(mf)])
        assert out.exit_code == 0
        assert "videofoundry" in out.output
        assert "representative" in out.output

    def test_index_not_found_exits_code_1(self, tmp_path: Path):
        from operations_center.artifact_index import ManifestNotFoundError
        with patch(_LOAD_MANIFEST_TARGET, side_effect=ManifestNotFoundError("missing")):
            out = _runner.invoke(app, ["index", "--manifest", "/nonexistent/manifest.json"])
        assert out.exit_code == 1
        assert "Not found" in out.output

    def test_index_invalid_manifest_exits_code_2(self, tmp_path: Path):
        from operations_center.artifact_index import ManifestInvalidError
        mf = _make_manifest_file(tmp_path)
        with patch(_LOAD_MANIFEST_TARGET, side_effect=ManifestInvalidError("bad schema")):
            out = _runner.invoke(app, ["index", "--manifest", str(mf)])
        assert out.exit_code == 2
        assert "Invalid manifest" in out.output


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    def test_list_empty(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        with patch(_LOAD_MANIFEST_TARGET), patch(_BUILD_INDEX_TARGET, return_value=index):
            out = _runner.invoke(app, ["list", "--manifest", str(mf)])
        assert out.exit_code == 0
        assert "No artifacts" in out.output

    def test_list_with_artifacts(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        art = _make_mock_artifact()
        index = _make_mock_index(artifacts=[art])
        with patch(_LOAD_MANIFEST_TARGET), patch(_BUILD_INDEX_TARGET, return_value=index):
            out = _runner.invoke(app, ["list", "--manifest", str(mf)])
        assert out.exit_code == 0
        assert "vf:rep:encode:output" in out.output


# ---------------------------------------------------------------------------
# cmd_get
# ---------------------------------------------------------------------------

class TestCmdGet:
    def test_get_existing_artifact(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        art = _make_mock_artifact()
        index = _make_mock_index(artifacts=[art])
        index.get_by_id = MagicMock(return_value=art)
        with patch(_LOAD_MANIFEST_TARGET), patch(_BUILD_INDEX_TARGET, return_value=index):
            out = _runner.invoke(
                app,
                ["get", "--manifest", str(mf), "--artifact-id", "vf:rep:encode:output"],
            )
        assert out.exit_code == 0
        assert "vf:rep:encode:output" in out.output

    def test_get_missing_artifact_exits_code_1(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        index.get_by_id = MagicMock(return_value=None)
        with patch(_LOAD_MANIFEST_TARGET), patch(_BUILD_INDEX_TARGET, return_value=index):
            out = _runner.invoke(
                app,
                ["get", "--manifest", str(mf), "--artifact-id", "no_such_id"],
            )
        assert out.exit_code == 1
        assert "Not found" in out.output


# ---------------------------------------------------------------------------
# cmd_query
# ---------------------------------------------------------------------------

class TestCmdQuery:
    def test_query_no_results(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        with (
            patch(_LOAD_MANIFEST_TARGET),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch("operations_center.entrypoints.artifacts.main.query_artifacts", return_value=[]),
        ):
            out = _runner.invoke(app, ["query", "--manifest", str(mf)])
        assert out.exit_code == 0
        assert "No matching" in out.output

    def test_query_with_results(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        art = _make_mock_artifact()
        index = _make_mock_index(artifacts=[art])
        with (
            patch(_LOAD_MANIFEST_TARGET),
            patch(_BUILD_INDEX_TARGET, return_value=index),
            patch("operations_center.entrypoints.artifacts.main.query_artifacts", return_value=[art]),
        ):
            out = _runner.invoke(app, ["query", "--manifest", str(mf)])
        assert out.exit_code == 0
        assert "vf:rep:encode:output" in out.output

    def test_query_invalid_location_exits_code_3(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        index = _make_mock_index()
        with patch(_LOAD_MANIFEST_TARGET), patch(_BUILD_INDEX_TARGET, return_value=index):
            out = _runner.invoke(app, ["query", "--manifest", str(mf), "--location", "not_a_real_location"])
        assert out.exit_code == 3
        assert "Invalid location" in out.output
