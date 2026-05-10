# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the artifact query API."""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.artifact_index import (
    ArtifactQuery,
    build_artifact_index,
    load_artifact_manifest,
    query_artifacts,
)
from operations_center.audit_contracts.vocabulary import (
    ArtifactStatus,
    ConsumerType,
    Limitation,
    Location,
    ValidFor,
)


@pytest.fixture()
def index_from_completed(example_completed_manifest_path: Path):
    manifest = load_artifact_manifest(example_completed_manifest_path)
    return build_artifact_index(manifest, example_completed_manifest_path)


@pytest.fixture()
def index_from_failed(example_failed_manifest_path: Path):
    manifest = load_artifact_manifest(example_failed_manifest_path)
    return build_artifact_index(manifest, example_failed_manifest_path)


class TestQueryArtifacts:
    def test_none_query_returns_all_artifacts(self, index_from_completed) -> None:
        results = query_artifacts(index_from_completed, None)
        assert results == index_from_completed.artifacts

    def test_empty_query_returns_all_artifacts(self, index_from_completed) -> None:
        results = query_artifacts(index_from_completed, ArtifactQuery())
        assert len(results) == len(index_from_completed.artifacts)

    def test_query_by_artifact_kind(self, index_from_completed) -> None:
        results = query_artifacts(index_from_completed, ArtifactQuery(artifact_kind="stage_report"))
        assert all(a.artifact_kind == "stage_report" for a in results)
        assert len(results) >= 1

    def test_query_by_location_run_root(self, index_from_completed) -> None:
        results = query_artifacts(index_from_completed, ArtifactQuery(location=Location.RUN_ROOT))
        assert all(a.location == Location.RUN_ROOT for a in results)
        assert len(results) >= 1

    def test_query_by_location_repo_singleton(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed, ArtifactQuery(location=Location.REPO_SINGLETON)
        )
        assert all(a.location == Location.REPO_SINGLETON for a in results)
        assert len(results) >= 1

    def test_query_by_source_stage(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed, ArtifactQuery(source_stage="TopicSelectionStage")
        )
        assert all(a.source_stage == "TopicSelectionStage" for a in results)
        assert len(results) >= 1

    def test_query_by_consumer_type(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed,
            ArtifactQuery(consumer_type=ConsumerType.HUMAN_REVIEW),
        )
        assert all(ConsumerType.HUMAN_REVIEW in a.consumer_types for a in results)
        assert len(results) >= 1

    def test_query_by_valid_for(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed,
            ArtifactQuery(valid_for=ValidFor.CURRENT_RUN_ONLY),
        )
        assert all(ValidFor.CURRENT_RUN_ONLY in a.valid_for for a in results)

    def test_query_by_limitation(self, index_from_failed) -> None:
        results = query_artifacts(
            index_from_failed,
            ArtifactQuery(limitation=Limitation.PARTIAL_RUN),
        )
        assert all(Limitation.PARTIAL_RUN in a.limitations for a in results)
        assert len(results) >= 1

    def test_query_by_is_repo_singleton_true(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed, ArtifactQuery(is_repo_singleton=True)
        )
        assert all(a.is_repo_singleton for a in results)
        assert len(results) >= 1

    def test_query_by_is_repo_singleton_false(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed, ArtifactQuery(is_repo_singleton=False)
        )
        assert all(not a.is_repo_singleton for a in results)
        assert len(results) >= 1

    def test_query_by_is_partial_true(self, index_from_failed) -> None:
        results = query_artifacts(index_from_failed, ArtifactQuery(is_partial=True))
        assert all(a.is_partial for a in results)

    def test_query_by_status_missing(self, index_from_failed) -> None:
        results = query_artifacts(
            index_from_failed, ArtifactQuery(status=ArtifactStatus.MISSING)
        )
        assert all(a.status == ArtifactStatus.MISSING for a in results)
        assert len(results) >= 1

    def test_query_by_content_type(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed, ArtifactQuery(content_type="application/json")
        )
        assert all(a.content_type == "application/json" for a in results)

    def test_combined_filters(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed,
            ArtifactQuery(
                location=Location.RUN_ROOT,
                consumer_type=ConsumerType.HUMAN_REVIEW,
            ),
        )
        for a in results:
            assert a.location == Location.RUN_ROOT
            assert ConsumerType.HUMAN_REVIEW in a.consumer_types

    def test_nonmatching_query_returns_empty(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed,
            ArtifactQuery(artifact_kind="nonexistent_kind_xyz"),
        )
        assert results == []

    def test_query_by_repo_id(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed, ArtifactQuery(repo_id="example_managed_repo")
        )
        assert len(results) == len(index_from_completed.artifacts)

    def test_query_by_wrong_repo_id_returns_empty(self, index_from_completed) -> None:
        results = query_artifacts(
            index_from_completed, ArtifactQuery(repo_id="otherrepo")
        )
        assert results == []

    def test_query_never_includes_excluded_paths(
        self, example_completed_manifest_path: Path
    ) -> None:
        manifest = load_artifact_manifest(example_completed_manifest_path)
        index = build_artifact_index(manifest, example_completed_manifest_path)

        excluded_paths = {ep.path for ep in index.excluded_paths}
        all_results = query_artifacts(index, ArtifactQuery())
        result_paths = {a.path for a in all_results}
        assert excluded_paths.isdisjoint(result_paths)
