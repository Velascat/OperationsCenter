"""Tests for ManagedArtifactManifest and ManagedArtifactEntry contract models."""

from __future__ import annotations

from datetime import datetime, timezone


from operations_center.audit_contracts.artifact_manifest import (
    ExcludedPath,
    ManagedArtifactEntry,
    ManagedArtifactManifest,
)
from operations_center.audit_contracts.vocabulary import (
    ArtifactStatus,
    ConsumerType,
    Limitation,
    Location,
    ManifestStatus,
    PathRole,
    ValidFor,
)

_NOW = datetime(2026, 4, 26, 15, 34, 55, tzinfo=timezone.utc)

_MINIMAL_MANIFEST = {
    "producer": "videofoundry",
    "repo_id": "videofoundry",
    "run_id": "3dead998d4c44e1cb296bef061de50f3",
    "audit_type": "representative",
    "manifest_status": "completed",
    "run_status": "completed",
    "created_at": _NOW.isoformat(),
    "updated_at": _NOW.isoformat(),
}

_MINIMAL_ARTIFACT = {
    "artifact_id": "videofoundry:representative:TopicSelectionStage:topic_selection",
    "artifact_kind": "stage_report",
    "path": "tools/audit/report/representative/bucket/topic_selection.json",
    "location": "run_root",
}


class TestManagedArtifactManifest:
    def test_parses_minimal(self) -> None:
        m = ManagedArtifactManifest.model_validate(_MINIMAL_MANIFEST)
        assert m.run_id == "3dead998d4c44e1cb296bef061de50f3"
        assert m.manifest_status == ManifestStatus.COMPLETED

    def test_schema_version_defaults(self) -> None:
        m = ManagedArtifactManifest.model_validate(_MINIMAL_MANIFEST)
        assert m.schema_version == "1.0"

    def test_contract_name_defaults(self) -> None:
        m = ManagedArtifactManifest.model_validate(_MINIMAL_MANIFEST)
        assert m.contract_name == "managed-repo-audit"

    def test_completed_is_terminal(self) -> None:
        m = ManagedArtifactManifest.model_validate(_MINIMAL_MANIFEST)
        assert m.is_terminal

    def test_failed_is_terminal(self) -> None:
        data = {**_MINIMAL_MANIFEST, "manifest_status": "failed"}
        assert ManagedArtifactManifest.model_validate(data).is_terminal

    def test_partial_is_terminal(self) -> None:
        data = {**_MINIMAL_MANIFEST, "manifest_status": "partial"}
        assert ManagedArtifactManifest.model_validate(data).is_terminal

    def test_running_is_not_terminal(self) -> None:
        data = {**_MINIMAL_MANIFEST, "manifest_status": "running"}
        assert not ManagedArtifactManifest.model_validate(data).is_terminal

    def test_initializing_is_not_terminal(self) -> None:
        data = {**_MINIMAL_MANIFEST, "manifest_status": "initializing"}
        assert not ManagedArtifactManifest.model_validate(data).is_terminal

    def test_empty_artifacts_allowed(self) -> None:
        m = ManagedArtifactManifest.model_validate({**_MINIMAL_MANIFEST, "manifest_status": "initializing"})
        assert m.artifacts == []

    def test_artifacts_added(self) -> None:
        data = {**_MINIMAL_MANIFEST, "artifacts": [_MINIMAL_ARTIFACT]}
        m = ManagedArtifactManifest.model_validate(data)
        assert len(m.artifacts) == 1

    def test_excluded_paths_accepted(self) -> None:
        data = {
            **_MINIMAL_MANIFEST,
            "excluded_paths": [{"path": "coverage.ini", "reason": "noise"}],
        }
        m = ManagedArtifactManifest.model_validate(data)
        assert len(m.excluded_paths) == 1

    def test_singleton_artifacts_property(self) -> None:
        singleton = {
            **_MINIMAL_ARTIFACT,
            "artifact_id": "videofoundry:repo_singleton:arch:latest",
            "location": "repo_singleton",
        }
        data = {**_MINIMAL_MANIFEST, "artifacts": [_MINIMAL_ARTIFACT, singleton]}
        m = ManagedArtifactManifest.model_validate(data)
        assert len(m.singleton_artifacts) == 1
        assert len(m.run_scoped_artifacts) == 1

    def test_artifact_by_id(self) -> None:
        data = {**_MINIMAL_MANIFEST, "artifacts": [_MINIMAL_ARTIFACT]}
        m = ManagedArtifactManifest.model_validate(data)
        found = m.artifact_by_id("videofoundry:representative:TopicSelectionStage:topic_selection")
        assert found is not None
        assert found.location == Location.RUN_ROOT

    def test_artifact_by_id_missing(self) -> None:
        data = {**_MINIMAL_MANIFEST, "artifacts": [_MINIMAL_ARTIFACT]}
        m = ManagedArtifactManifest.model_validate(data)
        assert m.artifact_by_id("nonexistent") is None

    def test_limitations_accepted(self) -> None:
        data = {**_MINIMAL_MANIFEST, "limitations": ["partial_run", "path_layout_non_uniform"]}
        m = ManagedArtifactManifest.model_validate(data)
        assert Limitation.PARTIAL_RUN in m.limitations


class TestManagedArtifactEntry:
    def test_parses_minimal(self) -> None:
        a = ManagedArtifactEntry.model_validate(_MINIMAL_ARTIFACT)
        assert a.location == Location.RUN_ROOT
        assert a.status == ArtifactStatus.PRESENT

    def test_path_role_defaults_unknown(self) -> None:
        a = ManagedArtifactEntry.model_validate(_MINIMAL_ARTIFACT)
        assert a.path_role == PathRole.UNKNOWN

    def test_relative_path_optional(self) -> None:
        a = ManagedArtifactEntry.model_validate(_MINIMAL_ARTIFACT)
        assert a.relative_path is None

    def test_repo_singleton_has_no_relative_path(self) -> None:
        data = {
            **_MINIMAL_ARTIFACT,
            "location": "repo_singleton",
            "relative_path": None,
        }
        a = ManagedArtifactEntry.model_validate(data)
        assert a.is_repo_singleton
        assert a.relative_path is None

    def test_artifacts_subdir_location(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "location": "artifacts_subdir"}
        a = ManagedArtifactEntry.model_validate(data)
        assert a.location == Location.ARTIFACTS_SUBDIR

    def test_audit_subdir_location(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "location": "audit_subdir"}
        a = ManagedArtifactEntry.model_validate(data)
        assert a.location == Location.AUDIT_SUBDIR

    def test_text_overlay_subdir_location(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "location": "text_overlay_subdir"}
        a = ManagedArtifactEntry.model_validate(data)
        assert a.location == Location.TEXT_OVERLAY_SUBDIR

    def test_repo_singleton_location(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "location": "repo_singleton"}
        a = ManagedArtifactEntry.model_validate(data)
        assert a.is_repo_singleton

    def test_missing_status_accepted(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "status": "missing"}
        a = ManagedArtifactEntry.model_validate(data)
        assert a.status == ArtifactStatus.MISSING
        assert not a.is_present

    def test_consumer_types_accepted(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "consumer_types": ["human_review", "fixture_harvesting"]}
        a = ManagedArtifactEntry.model_validate(data)
        assert ConsumerType.HUMAN_REVIEW in a.consumer_types

    def test_valid_for_accepted(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "valid_for": ["latest_snapshot"]}
        a = ManagedArtifactEntry.model_validate(data)
        assert ValidFor.LATEST_SNAPSHOT in a.valid_for

    def test_limitations_accepted(self) -> None:
        data = {**_MINIMAL_ARTIFACT, "limitations": ["partial_run", "missing_downstream_artifacts"]}
        a = ManagedArtifactEntry.model_validate(data)
        assert Limitation.PARTIAL_RUN in a.limitations


class TestExcludedPath:
    def test_minimal(self) -> None:
        ep = ExcludedPath(path="coverage.ini", reason="noise")
        assert ep.path == "coverage.ini"
        assert ep.pattern is None

    def test_with_pattern(self) -> None:
        ep = ExcludedPath(path=".coverage.dev.123", reason="noise", pattern=".coverage*")
        assert ep.pattern == ".coverage*"
