# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the managed-repo audit contract controlled vocabulary.

Verifies that:
- Generic enums contain all required values.
- Example managed-repo profile enums are clearly separated from generic enums.
- Round-trip serialisation works.
- All six VF audit types are present.
"""

from __future__ import annotations

import json


from operations_center.audit_contracts.vocabulary import (
    ConsumerType,
    GENERIC_ENUMS,
    Limitation,
    Location,
    ManifestStatus,
    RunStatus,
    ValidFor,
    ExampleManagedRepoArtifactKind,
    ExampleManagedRepoAuditType,
    ExampleManagedRepoSourceStage,
    EXAMPLE_MANAGED_REPO_PROFILE_ENUMS,
)


class TestGenericVsProfileSeparation:
    def test_generic_enums_are_distinct_from_profile_enums(self) -> None:
        generic_names = {e.__name__ for e in GENERIC_ENUMS}
        profile_names = {e.__name__ for e in EXAMPLE_MANAGED_REPO_PROFILE_ENUMS}
        assert generic_names.isdisjoint(profile_names), (
            "Generic enums must not overlap with example managed-repo profile enums"
        )

    def test_example_managed_repo_audit_type_is_not_in_generic_enums(self) -> None:
        assert ExampleManagedRepoAuditType not in GENERIC_ENUMS

    def test_run_status_is_generic(self) -> None:
        assert RunStatus in GENERIC_ENUMS

    def test_location_is_generic(self) -> None:
        assert Location in GENERIC_ENUMS


class TestRunStatus:
    def test_required_values_present(self) -> None:
        values = {m.value for m in RunStatus}
        required = {"pending", "running", "completed", "failed", "interrupted", "unknown"}
        assert required <= values

    def test_legacy_in_progress_present(self) -> None:
        assert RunStatus.IN_PROGRESS_LEGACY.value == "in_progress"

    def test_round_trip(self) -> None:
        assert RunStatus("completed") is RunStatus.COMPLETED
        assert RunStatus("in_progress") is RunStatus.IN_PROGRESS_LEGACY

    def test_json_serialises_as_string(self) -> None:
        data = json.dumps({"status": RunStatus.COMPLETED})
        assert '"completed"' in data


class TestManifestStatus:
    def test_required_values_present(self) -> None:
        values = {m.value for m in ManifestStatus}
        required = {"initializing", "running", "completed", "failed", "partial", "unknown"}
        assert required <= values

    def test_partial_is_present(self) -> None:
        assert ManifestStatus.PARTIAL.value == "partial"


class TestLocation:
    def test_required_values_present(self) -> None:
        values = {m.value for m in Location}
        required = {
            "run_root",
            "artifacts_subdir",
            "audit_subdir",
            "text_overlay_subdir",
            "repo_singleton",
            "external_or_unknown",
        }
        assert required <= values

    def test_repo_singleton_distinct_from_run_locations(self) -> None:
        run_locs = {Location.RUN_ROOT, Location.ARTIFACTS_SUBDIR, Location.AUDIT_SUBDIR, Location.TEXT_OVERLAY_SUBDIR}
        assert Location.REPO_SINGLETON not in run_locs


class TestConsumerType:
    def test_required_values_present(self) -> None:
        values = {m.value for m in ConsumerType}
        required = {
            "human_review",
            "automated_analysis",
            "fixture_harvesting",
            "slice_replay",
            "regression_testing",
            "architecture_invariant_verification",
            "failure_diagnosis",
            "unknown",
        }
        assert required <= values


class TestValidFor:
    def test_required_values_present(self) -> None:
        values = {m.value for m in ValidFor}
        required = {
            "current_run_only",
            "cross_run_comparison",
            "latest_snapshot",
            "historical_record",
            "partial_run_analysis",
            "unknown",
        }
        assert required <= values


class TestLimitation:
    def test_required_values_present(self) -> None:
        values = {m.value for m in Limitation}
        required = {
            "partial_run",
            "missing_downstream_artifacts",
            "producer_not_finalized",
            "non_representative_audit_unverified",
            "repo_singleton_overwritten",
            "infrastructure_noise_excluded",
            "path_layout_non_uniform",
            "unknown",
        }
        assert required <= values


class TestExampleManagedRepoProfile:
    def test_audit_types_present(self) -> None:
        values = {m.value for m in ExampleManagedRepoAuditType}
        assert values == {"audit_type_1", "audit_type_2"}

    def test_audit_types_are_strings(self) -> None:
        for m in ExampleManagedRepoAuditType:
            assert isinstance(m.value, str)

    def test_source_stage_includes_lifecycle_markers(self) -> None:
        values = {m.value for m in ExampleManagedRepoSourceStage}
        expected_subset = {
            "lifecycle",
            "post_run",
            "architecture_invariants",
            "unknown",
        }
        assert expected_subset <= values

    def test_artifact_kind_includes_required_kinds(self) -> None:
        values = {m.value for m in ExampleManagedRepoArtifactKind}
        expected_subset = {
            "run_status",
            "stage_report",
            "architecture_invariant",
            "unknown",
        }
        assert expected_subset <= values
