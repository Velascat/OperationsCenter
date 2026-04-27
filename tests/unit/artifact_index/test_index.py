"""Tests for the artifact index builder."""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.artifact_index import (
    ManagedArtifactIndex,
    build_artifact_index,
    load_artifact_manifest,
)
from operations_center.audit_contracts.vocabulary import (
    ArtifactStatus,
    Limitation,
    ManifestStatus,
    RunStatus,
)


class TestBuildArtifactIndex:
    def test_returns_managed_artifact_index(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert isinstance(index, ManagedArtifactIndex)

    def test_preserves_repo_id(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.source.repo_id == "videofoundry"

    def test_preserves_run_id(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.source.run_id == manifest.run_id

    def test_preserves_audit_type(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.source.audit_type == "representative"

    def test_indexes_all_manifest_artifacts(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert len(index.artifacts) == len(manifest.artifacts)

    def test_includes_repo_singleton_artifacts(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        singletons = index.singleton_artifacts
        assert len(singletons) >= 1

    def test_singleton_marked_is_repo_singleton(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        for s in index.singleton_artifacts:
            assert s.is_repo_singleton is True

    def test_run_scoped_not_marked_singleton(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        for a in index.run_scoped_artifacts:
            assert a.is_repo_singleton is False

    def test_preserves_partial_artifacts(self, failed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(failed_manifest_file)
        index = build_artifact_index(manifest, failed_manifest_file)
        # Partial manifest has a MISSING entry
        missing = [a for a in index.artifacts if a.status == ArtifactStatus.MISSING]
        assert len(missing) >= 1

    def test_partial_artifacts_marked_is_partial(self, failed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(failed_manifest_file)
        index = build_artifact_index(manifest, failed_manifest_file)
        partial = [a for a in index.artifacts if Limitation.PARTIAL_RUN in a.limitations]
        for p in partial:
            assert p.is_partial is True

    def test_non_partial_artifacts_not_marked_partial(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        for a in index.artifacts:
            if Limitation.PARTIAL_RUN not in a.limitations:
                assert a.is_partial is False

    def test_preserves_limitations_on_index(self, failed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(failed_manifest_file)
        index = build_artifact_index(manifest, failed_manifest_file)
        assert len(index.limitations) > 0

    def test_excluded_paths_retained_at_index_level(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        _index = build_artifact_index(manifest, completed_manifest_file)
        # completed_manifest_file fixture has no excluded_paths; use example instead

    def test_excluded_paths_from_example(self, example_completed_manifest_path: Path) -> None:
        manifest = load_artifact_manifest(example_completed_manifest_path)
        index = build_artifact_index(manifest, example_completed_manifest_path)
        assert len(index.excluded_paths) > 0

    def test_excluded_paths_not_indexed_as_artifacts(self, example_completed_manifest_path: Path) -> None:
        manifest = load_artifact_manifest(example_completed_manifest_path)
        index = build_artifact_index(manifest, example_completed_manifest_path)
        excluded_artifact_ids = {ep.path for ep in index.excluded_paths}
        artifact_paths = {a.path for a in index.artifacts}
        assert excluded_artifact_ids.isdisjoint(artifact_paths)

    def test_manifest_status_preserved(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.manifest_status == ManifestStatus.COMPLETED

    def test_run_status_preserved(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.run_status == RunStatus.COMPLETED

    def test_artifact_root_preserved(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.artifact_root == manifest.artifact_root

    def test_run_root_preserved(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.run_root == manifest.run_root

    def test_warnings_preserved(self, failed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(failed_manifest_file)
        index = build_artifact_index(manifest, failed_manifest_file)
        assert index.warnings == manifest.warnings

    def test_errors_preserved(self, failed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(failed_manifest_file)
        index = build_artifact_index(manifest, failed_manifest_file)
        assert index.errors == manifest.errors


class TestIndexPathResolution:
    def test_resolves_relative_path_with_run_root_heuristic(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        # Lay out files to match the path in the manifest
        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)
        artifact_file = tmp_path / completed_manifest_payload["artifacts"][0]["path"]
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text("{}", encoding="utf-8")

        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path)

        # The non-singleton artifact should resolve
        run_scoped = [a for a in index.run_scoped_artifacts if a.location != "external_or_unknown"]
        assert len(run_scoped) > 0
        first = run_scoped[0]
        assert first.resolved_path is not None
        assert first.resolved_path.is_absolute()

    def test_resolved_path_with_explicit_repo_root(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        first_run_scoped = index.run_scoped_artifacts[0]
        assert first_run_scoped.resolved_path is not None
        expected = (tmp_path / first_run_scoped.path).resolve()
        assert first_run_scoped.resolved_path == expected

    def test_exists_on_disk_true_for_present_file(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)

        # Create the artifact file
        entry_path = completed_manifest_payload["artifacts"][0]["path"]
        artifact_file = tmp_path / entry_path
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text("{}", encoding="utf-8")

        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        first = index.run_scoped_artifacts[0]
        assert first.exists_on_disk is True

    def test_exists_on_disk_false_for_missing_file(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)
        # Do NOT create the artifact file

        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path, repo_root=tmp_path)

        first = index.run_scoped_artifacts[0]
        assert first.exists_on_disk is False

    def test_is_machine_readable_for_json(
        self, tmp_path: Path, completed_manifest_payload
    ) -> None:
        run_root = completed_manifest_payload["run_root"]
        bucket_dir = tmp_path / run_root
        bucket_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = bucket_dir / "artifact_manifest.json"
        manifest_path.write_text(json.dumps(completed_manifest_payload), encoding="utf-8")

        manifest = load_artifact_manifest(manifest_path)
        index = build_artifact_index(manifest, manifest_path)

        json_artifacts = [a for a in index.artifacts if "json" in a.content_type]
        assert all(a.is_machine_readable for a in json_artifacts)


class TestIndexGetById:
    def test_get_by_id_returns_artifact(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        first = index.artifacts[0]
        found = index.get_by_id(first.artifact_id)
        assert found is not None
        assert found.artifact_id == first.artifact_id

    def test_get_by_id_returns_none_for_missing(self, completed_manifest_file: Path) -> None:
        manifest = load_artifact_manifest(completed_manifest_file)
        index = build_artifact_index(manifest, completed_manifest_file)
        assert index.get_by_id("no-such-artifact") is None
