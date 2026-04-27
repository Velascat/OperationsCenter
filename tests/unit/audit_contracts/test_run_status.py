"""Tests for the ManagedRunStatus contract model (run_status.json)."""

from __future__ import annotations


from operations_center.audit_contracts.run_status import ManagedRunStatus
from operations_center.audit_contracts.vocabulary import RunStatus


_MINIMAL_VALID = {
    "producer": "videofoundry",
    "repo_id": "videofoundry",
    "run_id": "3dead998d4c44e1cb296bef061de50f3",
    "audit_type": "representative",
    "status": "completed",
    "artifact_manifest_path": "tools/audit/report/representative/bucket/artifact_manifest.json",
}


class TestManagedRunStatus:
    def test_parses_minimal_valid(self) -> None:
        rs = ManagedRunStatus.model_validate(_MINIMAL_VALID)
        assert rs.run_id == "3dead998d4c44e1cb296bef061de50f3"
        assert rs.status == RunStatus.COMPLETED

    def test_schema_version_defaults(self) -> None:
        rs = ManagedRunStatus.model_validate(_MINIMAL_VALID)
        assert rs.schema_version == "1.0"

    def test_contract_name_defaults(self) -> None:
        rs = ManagedRunStatus.model_validate(_MINIMAL_VALID)
        assert rs.contract_name == "managed-repo-audit"

    def test_artifact_manifest_path_present(self) -> None:
        rs = ManagedRunStatus.model_validate(_MINIMAL_VALID)
        assert rs.artifact_manifest_path is not None
        assert "artifact_manifest.json" in rs.artifact_manifest_path

    def test_artifact_manifest_path_can_be_none(self) -> None:
        # Accepted during pre-Phase-5 transition; compliant runs must populate it.
        data = {**_MINIMAL_VALID, "artifact_manifest_path": None}
        rs = ManagedRunStatus.model_validate(data)
        assert rs.artifact_manifest_path is None
        assert rs.is_compliant is False

    def test_is_compliant_false_without_manifest_path(self) -> None:
        data = {**_MINIMAL_VALID, "artifact_manifest_path": None}
        rs = ManagedRunStatus.model_validate(data)
        assert not rs.is_compliant

    def test_is_compliant_true_with_manifest_path_and_running_status(self) -> None:
        data = {**_MINIMAL_VALID, "status": "completed"}
        rs = ManagedRunStatus.model_validate(data)
        assert rs.is_compliant

    def test_legacy_in_progress_accepted(self) -> None:
        data = {**_MINIMAL_VALID, "status": "in_progress"}
        rs = ManagedRunStatus.model_validate(data)
        assert rs.status == RunStatus.IN_PROGRESS_LEGACY
        assert not rs.is_compliant  # legacy value marks as non-compliant

    def test_status_completed_is_terminal(self) -> None:
        data = {**_MINIMAL_VALID, "status": "completed"}
        assert ManagedRunStatus.model_validate(data).is_terminal

    def test_status_failed_is_terminal(self) -> None:
        data = {**_MINIMAL_VALID, "status": "failed"}
        assert ManagedRunStatus.model_validate(data).is_terminal

    def test_status_interrupted_is_terminal(self) -> None:
        data = {**_MINIMAL_VALID, "status": "interrupted"}
        assert ManagedRunStatus.model_validate(data).is_terminal

    def test_status_running_is_not_terminal(self) -> None:
        data = {**_MINIMAL_VALID, "status": "running"}
        assert not ManagedRunStatus.model_validate(data).is_terminal

    def test_has_manifest_true(self) -> None:
        rs = ManagedRunStatus.model_validate(_MINIMAL_VALID)
        assert rs.has_manifest

    def test_has_manifest_false_when_none(self) -> None:
        data = {**_MINIMAL_VALID, "artifact_manifest_path": None}
        assert not ManagedRunStatus.model_validate(data).has_manifest

    def test_error_and_traceback_optional(self) -> None:
        data = {**_MINIMAL_VALID, "status": "failed", "error": "boom", "traceback": "..."}
        rs = ManagedRunStatus.model_validate(data)
        assert rs.error == "boom"
        assert rs.traceback == "..."

    def test_metadata_accepted(self) -> None:
        data = {**_MINIMAL_VALID, "metadata": {"channel_slug": "Connective_Contours"}}
        rs = ManagedRunStatus.model_validate(data)
        assert rs.metadata["channel_slug"] == "Connective_Contours"

    def test_all_status_values_accepted(self) -> None:
        for status in RunStatus:
            data = {**_MINIMAL_VALID, "status": status.value}
            rs = ManagedRunStatus.model_validate(data)
            assert rs.status == status
