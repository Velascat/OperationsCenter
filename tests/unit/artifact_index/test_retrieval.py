# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for the artifact retrieval API."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from operations_center.artifact_index import (
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
    ManifestInvalidError,
    NoManifestPathError,
    build_artifact_index,
    get_artifact_by_id,
    index_dispatch_result,
    load_artifact_manifest,
    read_json_artifact,
    read_text_artifact,
    resolve_artifact_path,
)


@pytest.fixture()
def index_with_real_files(tmp_path: Path, completed_manifest_payload):
    """Index whose run-scoped artifacts have real files on disk."""
    run_root = completed_manifest_payload["run_root"]
    bucket_dir = tmp_path / run_root
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # Write each artifact file
    for entry in completed_manifest_payload["artifacts"]:
        artifact_path = tmp_path / entry["path"]
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        if entry["content_type"] == "application/json":
            artifact_path.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        else:
            artifact_path.write_text("text content", encoding="utf-8")

    manifest_path = bucket_dir / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")

    manifest = load_artifact_manifest(manifest_path)
    return build_artifact_index(manifest, manifest_path, repo_root=tmp_path)


class TestGetArtifactById:
    def test_returns_expected_artifact(self, index_with_real_files) -> None:
        first = index_with_real_files.artifacts[0]
        found = get_artifact_by_id(index_with_real_files, first.artifact_id)
        assert found.artifact_id == first.artifact_id

    def test_raises_for_missing_id(self, index_with_real_files) -> None:
        with pytest.raises(ArtifactNotFoundError, match="not found"):
            get_artifact_by_id(index_with_real_files, "no-such-artifact")

    def test_error_includes_artifact_id(self, index_with_real_files) -> None:
        bad_id = "missing:artifact:id"
        with pytest.raises(ArtifactNotFoundError, match=bad_id):
            get_artifact_by_id(index_with_real_files, bad_id)


class TestResolveArtifactPath:
    def test_returns_path_for_resolvable_artifact(self, index_with_real_files) -> None:
        first = index_with_real_files.run_scoped_artifacts[0]
        path = resolve_artifact_path(index_with_real_files, first.artifact_id)
        assert isinstance(path, Path)
        assert path.is_absolute()

    def test_raises_for_missing_artifact_id(self, index_with_real_files) -> None:
        with pytest.raises(ArtifactNotFoundError):
            resolve_artifact_path(index_with_real_files, "no-such-artifact")

    def test_raises_for_unresolvable_path(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        # Make the artifact external_or_unknown so it can't be resolved
        import copy
        payload = copy.deepcopy(completed_manifest_payload)
        payload["artifacts"][0]["location"] = "external_or_unknown"
        payload["run_root"] = None  # also remove run_root to ensure no heuristic

        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path)

        first_id = index.artifacts[0].artifact_id
        with pytest.raises(ArtifactPathUnresolvableError):
            resolve_artifact_path(index, first_id)


class TestReadTextArtifact:
    def test_reads_text_content(self, index_with_real_files) -> None:
        json_artifacts = [
            a for a in index_with_real_files.run_scoped_artifacts
            if a.content_type == "application/json"
        ]
        assert json_artifacts, "need at least one JSON artifact"
        text = read_text_artifact(index_with_real_files, json_artifacts[0].artifact_id)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_respects_max_bytes(self, index_with_real_files) -> None:
        json_artifacts = [
            a for a in index_with_real_files.run_scoped_artifacts
            if a.content_type == "application/json"
        ]
        text = read_text_artifact(
            index_with_real_files, json_artifacts[0].artifact_id, max_bytes=5
        )
        assert len(text) <= 5

    def test_raises_for_missing_artifact_id(self, index_with_real_files) -> None:
        with pytest.raises(ArtifactNotFoundError):
            read_text_artifact(index_with_real_files, "no-such-artifact")

    def test_raises_for_unresolvable_path(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        import copy
        payload = copy.deepcopy(completed_manifest_payload)
        payload["artifacts"][0]["location"] = "external_or_unknown"
        payload["run_root"] = None

        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path)

        with pytest.raises(ArtifactPathUnresolvableError):
            read_text_artifact(index, index.artifacts[0].artifact_id)


class TestReadJsonArtifact:
    def test_reads_valid_json(self, index_with_real_files) -> None:
        json_artifacts = [
            a for a in index_with_real_files.run_scoped_artifacts
            if a.content_type == "application/json"
        ]
        result = read_json_artifact(index_with_real_files, json_artifacts[0].artifact_id)
        assert isinstance(result, dict)

    def test_raises_manifest_invalid_for_bad_json(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)

        entry = completed_manifest_payload["artifacts"][0]
        artifact_file = tmp_path / entry["path"]
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text("not valid json }{", encoding="utf-8")

        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        with pytest.raises(ManifestInvalidError, match="not valid JSON"):
            read_json_artifact(index, entry["artifact_id"])

    def test_raises_for_missing_artifact_id(self, index_with_real_files) -> None:
        with pytest.raises(ArtifactNotFoundError):
            read_json_artifact(index_with_real_files, "no-such-artifact")


class TestIndexDispatchResult:
    def test_raises_no_manifest_path_when_none(self) -> None:
        result = MagicMock()
        result.artifact_manifest_path = None
        with pytest.raises(NoManifestPathError):
            index_dispatch_result(result)

    def test_raises_no_manifest_path_when_missing_attr(self) -> None:
        class FakeResult:
            pass
        with pytest.raises(NoManifestPathError):
            index_dispatch_result(FakeResult())

    def test_loads_manifest_from_result_path(
        self, completed_manifest_file: Path
    ) -> None:
        result = MagicMock()
        result.artifact_manifest_path = str(completed_manifest_file)
        index = index_dispatch_result(result)
        assert index.source.repo_id == "videofoundry"

    def test_does_not_scan_directories(
        self, completed_manifest_file: Path, tmp_path: Path
    ) -> None:
        result = MagicMock()
        result.artifact_manifest_path = str(completed_manifest_file)
        # Create extra files that should not be picked up
        (completed_manifest_file.parent / "extra_artifact.json").write_text("{}", encoding="utf-8")
        index = index_dispatch_result(result)
        # Only artifacts from the manifest are indexed
        extra = index.get_by_id("extra-artifact-not-in-manifest")
        assert extra is None

    def test_rejects_missing_manifest_file(self) -> None:
        result = MagicMock()
        result.artifact_manifest_path = "/nonexistent/path/artifact_manifest.json"
        from operations_center.artifact_index import ManifestNotFoundError
        with pytest.raises(ManifestNotFoundError):
            index_dispatch_result(result)


class TestNoBoundaryViolation:
    def test_no_managed_repo_imports(self) -> None:
        import ast
        from pathlib import Path

        pkg_root = Path(__file__).resolve().parents[3] / "src" / "operations_center" / "artifact_index"
        for py_file in pkg_root.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("tools.audit"), (
                            f"{py_file.name} imports managed repo code: {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        assert not node.module.startswith("tools.audit"), (
                            f"{py_file.name} imports managed repo code: {node.module}"
                        )

    def test_no_directory_scanning(self) -> None:
        from pathlib import Path

        _FORBIDDEN = {"os.scandir", "os.listdir", "os.walk", "glob.glob", "glob.iglob", "Path.glob", "Path.rglob"}
        pkg_root = Path(__file__).resolve().parents[3] / "src" / "operations_center" / "artifact_index"
        for py_file in pkg_root.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            # Check that none of the obvious scandir/walk patterns appear
            for forbidden in ("os.scandir", "os.listdir", "os.walk"):
                assert forbidden not in source, (
                    f"{py_file.name} uses directory scanning: {forbidden}"
                )
