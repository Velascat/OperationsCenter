# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the manifest loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.artifact_index import (
    ManifestInvalidError,
    ManifestNotFoundError,
    load_artifact_manifest,
)
from operations_center.audit_contracts.vocabulary import ManifestStatus, RunStatus


class TestLoadArtifactManifest:
    def test_loads_completed_example(self, example_completed_manifest_path: Path) -> None:
        manifest = load_artifact_manifest(example_completed_manifest_path)
        assert manifest.manifest_status == ManifestStatus.COMPLETED
        assert manifest.run_status == RunStatus.COMPLETED
        assert manifest.repo_id == "example_managed_repo"
        assert len(manifest.artifacts) > 0

    def test_loads_failed_example(self, example_failed_manifest_path: Path) -> None:
        manifest = load_artifact_manifest(example_failed_manifest_path)
        assert manifest.manifest_status == ManifestStatus.PARTIAL
        assert manifest.run_status == RunStatus.INTERRUPTED
        assert len(manifest.artifacts) > 0

    def test_loads_completed_temp_file(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        assert manifest.manifest_status == ManifestStatus.COMPLETED

    def test_loads_failed_temp_file(self, failed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(failed_manifest_file)
        assert manifest.manifest_status == ManifestStatus.PARTIAL

    def test_rejects_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.json"
        with pytest.raises(ManifestNotFoundError, match="not found"):
            load_artifact_manifest(missing)

    def test_rejects_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(ManifestInvalidError, match="not valid JSON"):
            load_artifact_manifest(bad)

    def test_rejects_contract_invalid_manifest(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        # Missing required fields
        bad.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
        with pytest.raises(ManifestInvalidError, match="contract validation"):
            load_artifact_manifest(bad)

    def test_accepts_path_as_string(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(str(completed_manifest_file))
        assert manifest.repo_id == "example_managed_repo"

    def test_preserves_artifact_count(self, example_completed_manifest_path: Path) -> None:
        manifest = load_artifact_manifest(example_completed_manifest_path)
        # The completed example has 10 artifacts (verified from the JSON)
        assert len(manifest.artifacts) == 10

    def test_preserves_excluded_paths(self, example_completed_manifest_path: Path) -> None:
        manifest = load_artifact_manifest(example_completed_manifest_path)
        assert len(manifest.excluded_paths) > 0

    def test_preserves_limitations(self, example_failed_manifest_path: Path) -> None:
        manifest = load_artifact_manifest(example_failed_manifest_path)
        assert len(manifest.limitations) > 0
