"""Tests that contract example files validate against the Pydantic models.

These tests verify:
- All four example files parse without error.
- Required fields are populated in each example.
- artifact_manifest_path is present in run_status examples.
- All five location types appear in the completed manifest.
- Excluded infrastructure noise is separate from artifacts.
- Repo singleton artifact validates and has correct limitations.
- Failed/partial example has correct manifest_status and limitations.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.audit_contracts.artifact_manifest import (
    ManagedArtifactManifest,
)
from operations_center.audit_contracts.run_status import ManagedRunStatus
from operations_center.audit_contracts.vocabulary import (
    Limitation,
    Location,
    ManifestStatus,
    RunStatus,
    ValidFor,
)

_EXAMPLES = Path(__file__).parent.parent.parent.parent / "examples" / "audit_contracts"


def _load(filename: str) -> dict:
    data = json.loads((_EXAMPLES / filename).read_text())
    data.pop("_example_note", None)
    return data


@pytest.fixture(scope="module")
def completed_run_status() -> ManagedRunStatus:
    return ManagedRunStatus.model_validate(_load("completed_run_status.json"))


@pytest.fixture(scope="module")
def failed_run_status() -> ManagedRunStatus:
    return ManagedRunStatus.model_validate(_load("failed_run_status.json"))


@pytest.fixture(scope="module")
def completed_manifest() -> ManagedArtifactManifest:
    return ManagedArtifactManifest.model_validate(_load("completed_artifact_manifest.json"))


@pytest.fixture(scope="module")
def failed_manifest() -> ManagedArtifactManifest:
    return ManagedArtifactManifest.model_validate(_load("failed_artifact_manifest.json"))


class TestCompletedRunStatus:
    def test_parses(self, completed_run_status: ManagedRunStatus) -> None:
        assert completed_run_status is not None

    def test_status_completed(self, completed_run_status: ManagedRunStatus) -> None:
        assert completed_run_status.status == RunStatus.COMPLETED

    def test_artifact_manifest_path_present(self, completed_run_status: ManagedRunStatus) -> None:
        assert completed_run_status.artifact_manifest_path is not None
        assert "artifact_manifest.json" in completed_run_status.artifact_manifest_path

    def test_is_terminal(self, completed_run_status: ManagedRunStatus) -> None:
        assert completed_run_status.is_terminal

    def test_is_compliant(self, completed_run_status: ManagedRunStatus) -> None:
        assert completed_run_status.is_compliant

    def test_no_error(self, completed_run_status: ManagedRunStatus) -> None:
        assert completed_run_status.error is None


class TestFailedRunStatus:
    def test_parses(self, failed_run_status: ManagedRunStatus) -> None:
        assert failed_run_status is not None

    def test_status_interrupted(self, failed_run_status: ManagedRunStatus) -> None:
        assert failed_run_status.status == RunStatus.INTERRUPTED

    def test_artifact_manifest_path_present(self, failed_run_status: ManagedRunStatus) -> None:
        assert failed_run_status.artifact_manifest_path is not None

    def test_is_terminal(self, failed_run_status: ManagedRunStatus) -> None:
        assert failed_run_status.is_terminal

    def test_error_recorded(self, failed_run_status: ManagedRunStatus) -> None:
        assert failed_run_status.error is not None
        assert len(failed_run_status.error) > 0


class TestCompletedManifest:
    def test_parses(self, completed_manifest: ManagedArtifactManifest) -> None:
        assert completed_manifest is not None

    def test_manifest_status_completed(self, completed_manifest: ManagedArtifactManifest) -> None:
        assert completed_manifest.manifest_status == ManifestStatus.COMPLETED

    def test_has_artifacts(self, completed_manifest: ManagedArtifactManifest) -> None:
        assert len(completed_manifest.artifacts) > 0

    def test_has_run_root_artifact(self, completed_manifest: ManagedArtifactManifest) -> None:
        locs = {a.location for a in completed_manifest.artifacts}
        assert Location.RUN_ROOT in locs

    def test_has_artifacts_subdir_artifact(self, completed_manifest: ManagedArtifactManifest) -> None:
        locs = {a.location for a in completed_manifest.artifacts}
        assert Location.ARTIFACTS_SUBDIR in locs

    def test_has_audit_subdir_artifact(self, completed_manifest: ManagedArtifactManifest) -> None:
        locs = {a.location for a in completed_manifest.artifacts}
        assert Location.AUDIT_SUBDIR in locs

    def test_has_text_overlay_subdir_artifact(self, completed_manifest: ManagedArtifactManifest) -> None:
        locs = {a.location for a in completed_manifest.artifacts}
        assert Location.TEXT_OVERLAY_SUBDIR in locs

    def test_has_repo_singleton_artifact(self, completed_manifest: ManagedArtifactManifest) -> None:
        assert len(completed_manifest.singleton_artifacts) >= 1

    def test_repo_singleton_has_latest_snapshot(self, completed_manifest: ManagedArtifactManifest) -> None:
        for sa in completed_manifest.singleton_artifacts:
            assert ValidFor.LATEST_SNAPSHOT in sa.valid_for

    def test_repo_singleton_has_overwritten_limitation(self, completed_manifest: ManagedArtifactManifest) -> None:
        for sa in completed_manifest.singleton_artifacts:
            assert Limitation.REPO_SINGLETON_OVERWRITTEN in sa.limitations

    def test_excluded_paths_present(self, completed_manifest: ManagedArtifactManifest) -> None:
        assert len(completed_manifest.excluded_paths) >= 1

    def test_excluded_paths_not_in_artifacts(self, completed_manifest: ManagedArtifactManifest) -> None:
        artifact_paths = {a.path for a in completed_manifest.artifacts}
        for ep in completed_manifest.excluded_paths:
            assert ep.path not in artifact_paths, (
                f"Excluded path {ep.path!r} must not also appear in artifacts"
            )

    def test_coverage_ini_excluded(self, completed_manifest: ManagedArtifactManifest) -> None:
        excluded_paths = {ep.path for ep in completed_manifest.excluded_paths}
        assert any("coverage.ini" in p for p in excluded_paths)

    def test_is_terminal(self, completed_manifest: ManagedArtifactManifest) -> None:
        assert completed_manifest.is_terminal


class TestFailedManifest:
    def test_parses(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert failed_manifest is not None

    def test_manifest_status_partial(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert failed_manifest.manifest_status == ManifestStatus.PARTIAL

    def test_run_status_interrupted(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert failed_manifest.run_status == RunStatus.INTERRUPTED

    def test_has_partial_run_limitation(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert Limitation.PARTIAL_RUN in failed_manifest.limitations

    def test_has_missing_downstream_limitation(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert Limitation.MISSING_DOWNSTREAM_ARTIFACTS in failed_manifest.limitations

    def test_some_artifacts_missing(self, failed_manifest: ManagedArtifactManifest) -> None:
        from operations_center.audit_contracts.vocabulary import ArtifactStatus
        missing = [a for a in failed_manifest.artifacts if a.status == ArtifactStatus.MISSING]
        assert len(missing) >= 1, "Failed manifest should have at least one missing artifact"

    def test_has_errors(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert len(failed_manifest.errors) >= 1

    def test_excluded_paths_present(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert len(failed_manifest.excluded_paths) >= 1

    def test_has_repo_singleton(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert len(failed_manifest.singleton_artifacts) >= 1

    def test_is_terminal(self, failed_manifest: ManagedArtifactManifest) -> None:
        assert failed_manifest.is_terminal
