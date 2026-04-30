# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for fixture artifact selection."""

from __future__ import annotations


import pytest

from operations_center.fixture_harvesting import (
    HarvestProfile,
    HarvestRequest,
    select_fixture_artifacts,
    HarvestInputError,
)



def _make_request(index, profile: HarvestProfile, **kwargs) -> HarvestRequest:
    return HarvestRequest(index=index, harvest_profile=profile, **kwargs)


class TestManualSelection:
    def test_selects_explicit_artifact_ids(self, completed_index) -> None:
        aid = completed_index.artifacts[0].artifact_id
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(completed_index, HarvestProfile.MANUAL_SELECTION, artifact_ids=[aid]),
        )
        assert aid in sel.artifact_ids

    def test_manual_selection_preserves_order(self, completed_index) -> None:
        ids = [a.artifact_id for a in completed_index.artifacts if not a.is_repo_singleton]
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(
                completed_index, HarvestProfile.MANUAL_SELECTION,
                artifact_ids=ids, include_repo_singletons=False,
            ),
        )
        assert sel.artifact_ids == ids

    def test_manual_selection_raises_for_unknown_id(self, completed_index) -> None:
        with pytest.raises(HarvestInputError, match="not found in index"):
            select_fixture_artifacts(
                completed_index,
                _make_request(
                    completed_index, HarvestProfile.MANUAL_SELECTION,
                    artifact_ids=["nonexistent:id"],
                ),
            )

    def test_manual_selection_requires_artifact_ids(self, completed_index) -> None:
        with pytest.raises(HarvestInputError):
            select_fixture_artifacts(
                completed_index,
                _make_request(completed_index, HarvestProfile.MANUAL_SELECTION),
            )


class TestStageSlice:
    def test_stage_slice_filters_by_source_stage(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(
                completed_index, HarvestProfile.STAGE_SLICE,
                source_stage="TopicSelectionStage",
            ),
        )
        for s in sel.selected:
            assert s.artifact.source_stage == "TopicSelectionStage"

    def test_stage_slice_requires_source_stage(self, completed_index) -> None:
        with pytest.raises(HarvestInputError, match="source_stage"):
            select_fixture_artifacts(
                completed_index,
                _make_request(completed_index, HarvestProfile.STAGE_SLICE),
            )

    def test_stage_slice_empty_when_stage_not_present(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(
                completed_index, HarvestProfile.STAGE_SLICE,
                source_stage="NonexistentStage",
            ),
        )
        assert len(sel.selected) == 0


class TestArtifactKindFilter:
    def test_kind_filter_restricts_selection(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(
                completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT,
                artifact_kind="stage_report",
                include_repo_singletons=True,
            ),
        )
        for s in sel.selected:
            assert s.artifact.artifact_kind == "stage_report"


class TestMinimalFailure:
    def test_selects_missing_artifacts(self, failed_index) -> None:
        sel = select_fixture_artifacts(
            failed_index,
            _make_request(failed_index, HarvestProfile.MINIMAL_FAILURE),
        )
        rationales = [s.rationale for s in sel.selected]
        assert any("missing" in r or "partial" in r for r in rationales)

    def test_empty_index_returns_no_selections(self, empty_index) -> None:
        sel = select_fixture_artifacts(
            empty_index,
            _make_request(empty_index, HarvestProfile.MINIMAL_FAILURE),
        )
        assert len(sel.selected) == 0


class TestPartialRun:
    def test_detects_partial_run_limitations(self, failed_index) -> None:
        sel = select_fixture_artifacts(
            failed_index,
            _make_request(failed_index, HarvestProfile.PARTIAL_RUN),
        )
        assert len(sel.selected) >= 1

    def test_partial_run_rationale_mentions_partial(self, failed_index) -> None:
        sel = select_fixture_artifacts(
            failed_index,
            _make_request(failed_index, HarvestProfile.PARTIAL_RUN),
        )
        assert any("partial" in s.rationale for s in sel.selected)


class TestArtifactHealth:
    def test_selects_missing_file_artifacts(self, failed_index) -> None:
        sel = select_fixture_artifacts(
            failed_index,
            _make_request(failed_index, HarvestProfile.ARTIFACT_HEALTH),
        )
        assert len(sel.selected) >= 1


class TestFullManifestSnapshot:
    def test_selects_all_non_singleton_artifacts(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT),
        )
        non_singletons = [a for a in completed_index.artifacts if not a.is_repo_singleton]
        assert len(sel.selected) == len(non_singletons)


class TestSingletonHandling:
    def test_repo_singletons_excluded_by_default(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT),
        )
        for s in sel.selected:
            assert not s.artifact.is_repo_singleton

    def test_repo_singletons_included_when_requested(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(
                completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT,
                include_repo_singletons=True,
            ),
        )
        singleton_ids = {a.artifact_id for a in completed_index.singleton_artifacts}
        selected_ids = set(sel.artifact_ids)
        assert singleton_ids & selected_ids  # at least one singleton included


class TestDeterministicOrdering:
    def test_selection_order_matches_index_order(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(completed_index, HarvestProfile.PRODUCER_COMPLIANCE),
        )
        index_order = [a.artifact_id for a in completed_index.artifacts if not a.is_repo_singleton]
        sel_order = [s.artifact.artifact_id for s in sel.selected]
        assert sel_order == index_order


class TestMaxArtifacts:
    def test_max_artifacts_limits_selection(self, completed_index) -> None:
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(
                completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT,
                max_artifacts=1, include_repo_singletons=True,
            ),
        )
        assert len(sel.selected) <= 1


class TestSkippedTracking:
    def test_skipped_ids_are_recorded(self, completed_index) -> None:
        # Select only non-singleton artifacts; singletons should be in skipped
        sel = select_fixture_artifacts(
            completed_index,
            _make_request(completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT),
        )
        singleton_ids = {a.artifact_id for a in completed_index.singleton_artifacts}
        assert singleton_ids.issubset(set(sel.skipped_ids))
