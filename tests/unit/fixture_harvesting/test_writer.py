# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for fixture pack writer."""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.artifact_index import build_artifact_index, load_artifact_manifest
from operations_center.fixture_harvesting import (
    CopyPolicy,
    HarvestProfile,
    HarvestRequest,
    harvest_fixtures,
)

from .conftest import (
    _base_entry,
    _make_manifest_payload,
    _write_manifest,
)


def _harvest(index, profile: HarvestProfile, output_dir: Path, **kwargs) -> tuple:
    request = HarvestRequest(index=index, harvest_profile=profile, **kwargs)
    return harvest_fixtures(request, output_dir)


class TestWriterCreatesDirectory:
    def test_creates_fixture_pack_json(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        assert (pack_dir / "fixture_pack.json").exists()

    def test_creates_artifacts_directory(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        assert (pack_dir / "artifacts").is_dir()

    def test_creates_source_index_summary(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        assert (pack_dir / "source_index_summary.json").exists()

    def test_pack_dir_name_is_fixture_pack_id(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path
        )
        assert pack_dir.name == pack.fixture_pack_id

    def test_nested_output_dir_created(self, tmp_path: Path, completed_index) -> None:
        nested = tmp_path / "deep" / "output"
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.MINIMAL_FAILURE, nested
        )
        assert pack_dir.exists()


class TestWriterCopiesArtifacts:
    def test_copies_existing_json_artifact(self, tmp_path: Path, index_with_real_file) -> None:
        pack, pack_dir = _harvest(
            index_with_real_file, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        copied = [a for a in pack.artifacts if a.copied]
        assert len(copied) >= 1
        for fa in copied:
            assert (pack_dir / "artifacts" / fa.fixture_relative_path).exists()

    def test_copied_artifact_content_matches_source(
        self, tmp_path: Path, index_with_real_file
    ) -> None:
        pack, pack_dir = _harvest(
            index_with_real_file, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        for fa in pack.artifacts:
            if fa.copied and fa.fixture_relative_path:
                copied_data = json.loads(
                    (pack_dir / "artifacts" / fa.fixture_relative_path).read_text()
                )
                assert copied_data.get("key") == "value"

    def test_copies_multiple_artifacts(self, tmp_path: Path, index_with_multiple_real_files) -> None:
        pack, pack_dir = _harvest(
            index_with_multiple_real_files, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        assert pack.copied_count == 2


class TestWriterMetadataOnly:
    def test_missing_file_recorded_as_not_copied(self, tmp_path: Path, failed_index) -> None:
        pack, pack_dir = _harvest(
            failed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path
        )
        metadata_only = [a for a in pack.artifacts if not a.copied]
        assert len(metadata_only) >= 1

    def test_missing_file_has_copy_error(self, tmp_path: Path, failed_index) -> None:
        pack, pack_dir = _harvest(
            failed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path
        )
        for fa in pack.artifacts:
            if not fa.copied:
                assert fa.copy_error != ""

    def test_unresolved_path_recorded_as_not_copied(self, tmp_path: Path, completed_index) -> None:
        # completed_index has no repo_root → paths unresolved
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        # All artifacts should be metadata-only since paths can't resolve
        assert all(not fa.copied for fa in pack.artifacts)
        assert all(fa.copy_error != "" for fa in pack.artifacts)


class TestCopyPolicy:
    def test_enforces_max_artifact_bytes(self, tmp_path: Path, index_with_real_file) -> None:
        pack, pack_dir = _harvest(
            index_with_real_file, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path,
            copy_policy=CopyPolicy(max_artifact_bytes=1),  # 1 byte — everything skipped
        )
        # All artifacts either skipped or metadata-only due to size
        assert all(not fa.copied for fa in pack.artifacts)

    def test_enforces_max_total_bytes(self, tmp_path: Path, index_with_multiple_real_files) -> None:
        pack, pack_dir = _harvest(
            index_with_multiple_real_files, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path,
            copy_policy=CopyPolicy(max_total_bytes=1),  # 1 byte total
        )
        # At most 0 or 1 files copied since total budget is 1 byte
        assert pack.copied_count <= 1

    def test_skips_binary_artifacts_by_default(self, tmp_path: Path) -> None:
        binary_entry = {
            "artifact_id": "videofoundry:representative:SomeStage:binary",
            "artifact_kind": "binary_blob",
            "path": "tools/audit/report/representative/Bucket_run999/blob.bin",
            "relative_path": "blob.bin",
            "location": "run_root",
            "path_role": "primary",
            "source_stage": "SomeStage",
            "status": "present",
            "created_at": None, "updated_at": None,
            "size_bytes": 100,
            "content_type": "application/octet-stream",
            "checksum": None,
            "consumer_types": [],
            "valid_for": [],
            "limitations": [],
            "description": "",
            "metadata": {},
        }
        payload = _make_manifest_payload(artifacts=[binary_entry])
        manifest_path = _write_manifest(tmp_path, payload)

        blob_path = tmp_path / binary_entry["path"]
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(b"\x00" * 100)

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        pack, pack_dir = _harvest(index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path / "out")
        for fa in pack.artifacts:
            assert not fa.copied
            assert "binary" in fa.copy_error.lower() or "content type" in fa.copy_error.lower()

    def test_binary_artifacts_copied_when_allowed(self, tmp_path: Path) -> None:
        binary_entry = {
            "artifact_id": "videofoundry:representative:SomeStage:binary",
            "artifact_kind": "binary_blob",
            "path": "tools/audit/report/representative/Bucket_run999/blob.bin",
            "relative_path": "blob.bin",
            "location": "run_root",
            "path_role": "primary",
            "source_stage": "SomeStage",
            "status": "present",
            "created_at": None, "updated_at": None,
            "size_bytes": 4,
            "content_type": "application/octet-stream",
            "checksum": None,
            "consumer_types": [],
            "valid_for": [],
            "limitations": [],
            "description": "",
            "metadata": {},
        }
        payload = _make_manifest_payload(artifacts=[binary_entry])
        manifest_path = _write_manifest(tmp_path, payload)

        blob_path = tmp_path / binary_entry["path"]
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_bytes(b"\x00\x01\x02\x03")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        pack, pack_dir = _harvest(
            index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path / "out",
            copy_policy=CopyPolicy(include_binary_artifacts=True),
        )
        copied = [fa for fa in pack.artifacts if fa.copied]
        assert len(copied) >= 1


class TestProvenanceFiles:
    def test_source_manifest_copied_when_exists(self, tmp_path: Path, index_with_real_file) -> None:
        pack, pack_dir = _harvest(
            index_with_real_file, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        assert (pack_dir / "source_manifest.json").exists()

    def test_source_index_summary_is_valid_json(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        data = json.loads((pack_dir / "source_index_summary.json").read_text())
        assert "total_artifacts" in data

    def test_source_manifest_path_recorded_in_pack(self, tmp_path: Path, completed_index) -> None:
        pack, _ = _harvest(
            completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        assert pack.source_manifest_path != ""


class TestPackContents:
    def test_pack_records_harvest_profile(self, tmp_path: Path, completed_index) -> None:
        pack, _ = _harvest(
            completed_index, HarvestProfile.PRODUCER_COMPLIANCE, tmp_path
        )
        assert pack.harvest_profile == HarvestProfile.PRODUCER_COMPLIANCE

    def test_pack_records_source_identity(self, tmp_path: Path, completed_index) -> None:
        pack, _ = _harvest(
            completed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path
        )
        assert pack.source_repo_id == "videofoundry"
        assert pack.source_run_id == "run999"
        assert pack.source_audit_type == "representative"

    def test_pack_fixture_pack_json_is_valid(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path
        )
        data = json.loads((pack_dir / "fixture_pack.json").read_text())
        assert data["schema_version"] == "1.0"
        assert data["fixture_pack_id"] == pack.fixture_pack_id

    def test_pack_includes_selection_rationale(self, tmp_path: Path, completed_index) -> None:
        pack, _ = _harvest(
            completed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path,
            selection_rationale="testing rationale recording",
        )
        assert pack.selection_rationale == "testing rationale recording"


class TestExcludedPathsNotHarvested:
    def test_excluded_paths_not_in_artifacts(self, tmp_path: Path) -> None:
        # Build index with excluded paths — they must not appear in selected artifacts
        from operations_center.artifact_index import build_artifact_index, load_artifact_manifest
        payload = _make_manifest_payload(
            artifacts=[_base_entry()],
            excluded_paths=[
                {"path": "coverage.ini", "reason": "noise", "pattern": "coverage.ini"},
            ],
        )
        manifest_path = _write_manifest(tmp_path, payload)
        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path)

        pack, _ = _harvest(index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path / "out")
        artifact_paths = {fa.source_path for fa in pack.artifacts}
        assert "coverage.ini" not in artifact_paths


class TestNoSourceMutation:
    def test_source_artifact_file_unchanged(self, tmp_path: Path, index_with_real_file) -> None:
        src_content = json.dumps({"key": "value", "stage": "topic_selection"})
        pack, pack_dir = _harvest(
            index_with_real_file, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        # Read source file to verify unchanged
        src = index_with_real_file.artifacts[0].resolved_path
        if src and src.exists():
            assert src.read_text() == src_content

    def test_source_index_artifact_list_unchanged(
        self, tmp_path: Path, completed_index
    ) -> None:
        original_count = len(completed_index.artifacts)
        _harvest(completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path)
        assert len(completed_index.artifacts) == original_count


class TestFindingReferences:
    def test_finding_references_preserved_in_pack(
        self, tmp_path: Path, failed_index
    ) -> None:
        from operations_center.behavior_calibration import (
            AnalysisProfile,
            BehaviorCalibrationInput,
            analyze_artifacts,
        )
        inp = BehaviorCalibrationInput(
            repo_id=failed_index.source.repo_id,
            run_id=failed_index.source.run_id,
            audit_type=failed_index.source.audit_type,
            artifact_index=failed_index,
            analysis_profile=AnalysisProfile.FAILURE_DIAGNOSIS,
        )
        report = analyze_artifacts(inp)

        request = HarvestRequest(
            index=failed_index,
            harvest_profile=HarvestProfile.MINIMAL_FAILURE,
            findings=report.findings,
            finding_ids=[f.finding_id for f in report.findings],
        )
        pack, _ = harvest_fixtures(request, tmp_path)
        assert len(pack.findings) >= 1
        for ref in pack.findings:
            assert ref.source_finding_id != ""

    def test_recommendations_not_treated_as_actions(
        self, tmp_path: Path, failed_index
    ) -> None:
        from operations_center.behavior_calibration import (
            AnalysisProfile,
            BehaviorCalibrationInput,
            analyze_artifacts,
        )
        inp = BehaviorCalibrationInput(
            repo_id=failed_index.source.repo_id,
            run_id=failed_index.source.run_id,
            audit_type=failed_index.source.audit_type,
            artifact_index=failed_index,
            analysis_profile=AnalysisProfile.RECOMMENDATION,
        )
        report = analyze_artifacts(inp)
        # recommendations exist but must not appear as executable in fixture pack
        assert all(rec.requires_human_review for rec in report.recommendations)
        # fixture harvesting does not store recommendations as actions
        request = HarvestRequest(
            index=failed_index,
            harvest_profile=HarvestProfile.MINIMAL_FAILURE,
            findings=report.findings,
        )
        pack, _ = harvest_fixtures(request, tmp_path)
        # pack has no "recommendations" or "actions" field
        pack_data = json.loads(pack.model_dump_json())
        assert "recommendations" not in pack_data
        assert "actions" not in pack_data
