# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for fixture harvesting models."""

from __future__ import annotations

import json
import re

import pytest

from operations_center.fixture_harvesting import (
    CopyPolicy,
    FixtureArtifact,
    FixtureFindingReference,
    FixturePack,
    HarvestProfile,
    make_fixture_pack_id,
)
from operations_center.behavior_calibration.models import ArtifactIndexSummary


def _make_summary() -> ArtifactIndexSummary:
    return ArtifactIndexSummary(
        total_artifacts=2,
        by_kind={"stage_report": 2},
        by_location={"run_root": 2},
        by_status={"present": 2},
        singleton_count=0,
        partial_count=0,
        excluded_path_count=0,
        unresolved_path_count=0,
        missing_file_count=0,
        machine_readable_count=1,
        warnings_count=0,
        errors_count=0,
        manifest_limitations=[],
    )


def _make_fixture_artifact(*, copied: bool = True) -> FixtureArtifact:
    return FixtureArtifact(
        source_artifact_id="videofoundry:representative:SomeStage:artifact",
        artifact_kind="stage_report",
        source_stage="SomeStage",
        location="run_root",
        path_role="primary",
        source_path="tools/audit/report/representative/run999/artifact.json",
        fixture_relative_path="artifact.json" if copied else None,
        content_type="application/json",
        copied=copied,
        copy_error="" if copied else "file missing",
    )


def _make_pack() -> FixturePack:
    return FixturePack(
        fixture_pack_id="videofoundry__run999__minimal_failure__20260426_120000",
        source_repo_id="videofoundry",
        source_run_id="run999",
        source_audit_type="representative",
        source_manifest_path="/tmp/manifest.json",
        source_index_summary=_make_summary(),
        harvest_profile=HarvestProfile.MINIMAL_FAILURE,
    )


class TestFixturePackId:
    def test_make_fixture_pack_id_is_path_safe(self) -> None:
        pack_id = make_fixture_pack_id("videofoundry", "run999", HarvestProfile.MINIMAL_FAILURE)
        assert re.match(r"^[a-zA-Z0-9_\-]+$", pack_id), f"Not path-safe: {pack_id!r}"

    def test_make_fixture_pack_id_contains_repo_id(self) -> None:
        pack_id = make_fixture_pack_id("videofoundry", "run999", HarvestProfile.MINIMAL_FAILURE)
        assert "videofoundry" in pack_id

    def test_make_fixture_pack_id_contains_run_id(self) -> None:
        pack_id = make_fixture_pack_id("videofoundry", "run999", HarvestProfile.MINIMAL_FAILURE)
        assert "run999" in pack_id

    def test_make_fixture_pack_id_contains_profile(self) -> None:
        pack_id = make_fixture_pack_id("videofoundry", "run999", HarvestProfile.STAGE_SLICE)
        assert "stage_slice" in pack_id

    def test_make_fixture_pack_id_no_spaces(self) -> None:
        pack_id = make_fixture_pack_id("my repo", "run 1", HarvestProfile.FULL_MANIFEST_SNAPSHOT)
        assert " " not in pack_id

    def test_make_fixture_pack_id_no_colons(self) -> None:
        pack_id = make_fixture_pack_id("videofoundry", "run:999", HarvestProfile.MANUAL_SELECTION)
        assert ":" not in pack_id


class TestFixtureArtifact:
    def test_fixture_artifact_has_auto_id(self) -> None:
        fa = _make_fixture_artifact()
        assert fa.fixture_artifact_id
        assert len(fa.fixture_artifact_id) > 0

    def test_fixture_artifact_is_frozen(self) -> None:
        fa = _make_fixture_artifact()
        with pytest.raises(Exception):
            fa.copied = False  # type: ignore[misc]

    def test_fixture_artifact_references_source_id(self) -> None:
        fa = _make_fixture_artifact()
        assert fa.source_artifact_id == "videofoundry:representative:SomeStage:artifact"

    def test_fixture_artifact_copied_false_has_error(self) -> None:
        fa = _make_fixture_artifact(copied=False)
        assert fa.copied is False
        assert fa.copy_error != ""

    def test_fixture_artifact_serializes_to_json(self) -> None:
        fa = _make_fixture_artifact()
        data = json.loads(fa.model_dump_json())
        assert data["copied"] is True
        assert data["source_artifact_id"] == "videofoundry:representative:SomeStage:artifact"


class TestFixtureFindingReference:
    def test_finding_reference_is_frozen(self) -> None:
        ref = FixtureFindingReference(
            source_finding_id="abc-123",
            severity="error",
            category="failed_run",
            summary="run failed",
        )
        with pytest.raises(Exception):
            ref.summary = "mutated"  # type: ignore[misc]

    def test_finding_reference_has_no_executable_field(self) -> None:
        ref = FixtureFindingReference(
            source_finding_id="abc-123",
            severity="warning",
            category="partial_run",
            summary="partial run",
        )
        assert not hasattr(ref, "apply")
        assert not hasattr(ref, "execute")


class TestFixturePack:
    def test_fixture_pack_serializes_to_json(self) -> None:
        pack = _make_pack()
        data = json.loads(pack.model_dump_json())
        assert data["schema_version"] == "1.0"
        assert data["source_repo_id"] == "videofoundry"

    def test_fixture_pack_artifact_count(self) -> None:
        pack = _make_pack()
        pack2 = pack.model_copy(update={"artifacts": [_make_fixture_artifact(), _make_fixture_artifact(copied=False)]})
        assert pack2.artifact_count == 2
        assert pack2.copied_count == 1
        assert pack2.metadata_only_count == 1

    def test_fixture_pack_requires_source_manifest_path(self) -> None:
        pack = _make_pack()
        assert pack.source_manifest_path != ""

    def test_fixture_pack_harvest_profile_recorded(self) -> None:
        pack = _make_pack()
        assert pack.harvest_profile == HarvestProfile.MINIMAL_FAILURE

    def test_fixture_pack_all_profiles_valid(self) -> None:
        for profile in HarvestProfile:
            pack_id = make_fixture_pack_id("repo", "run1", profile)
            assert profile.value in pack_id


class TestCopyPolicy:
    def test_copy_policy_defaults(self) -> None:
        policy = CopyPolicy()
        assert policy.max_artifact_bytes == 10 * 1024 * 1024
        assert policy.max_total_bytes == 100 * 1024 * 1024
        assert policy.include_binary_artifacts is False
        assert policy.include_missing_files is True
        assert policy.allowed_content_types is None

    def test_copy_policy_custom_max(self) -> None:
        policy = CopyPolicy(max_artifact_bytes=1024, max_total_bytes=4096)
        assert policy.max_artifact_bytes == 1024
        assert policy.max_total_bytes == 4096
