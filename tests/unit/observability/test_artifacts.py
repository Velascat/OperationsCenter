# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for observability/artifacts.py — ArtifactNormalizer and ArtifactIndex."""

from __future__ import annotations

import pytest

from operations_center.contracts.enums import ArtifactType
from operations_center.observability.artifacts import ArtifactNormalizer

from .conftest import make_artifact


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------


def test_empty_artifacts_produces_empty_index():
    idx = ArtifactNormalizer.index([])
    assert idx.primary_artifacts == []
    assert idx.supplemental_artifacts == []
    assert idx.artifact_counts == {}
    assert idx.artifact_types_present == []


# ---------------------------------------------------------------------------
# Primary classification
# ---------------------------------------------------------------------------


def test_diff_is_primary():
    a = make_artifact(ArtifactType.DIFF)
    idx = ArtifactNormalizer.index([a])
    assert a in idx.primary_artifacts
    assert idx.supplemental_artifacts == []


def test_patch_is_primary():
    a = make_artifact(ArtifactType.PATCH)
    idx = ArtifactNormalizer.index([a])
    assert a in idx.primary_artifacts


def test_validation_report_is_primary():
    a = make_artifact(ArtifactType.VALIDATION_REPORT)
    idx = ArtifactNormalizer.index([a])
    assert a in idx.primary_artifacts


# ---------------------------------------------------------------------------
# Supplemental classification
# ---------------------------------------------------------------------------


def test_log_excerpt_is_supplemental():
    a = make_artifact(ArtifactType.LOG_EXCERPT)
    idx = ArtifactNormalizer.index([a])
    assert a in idx.supplemental_artifacts
    assert idx.primary_artifacts == []


def test_goal_file_is_supplemental():
    a = make_artifact(ArtifactType.GOAL_FILE)
    idx = ArtifactNormalizer.index([a])
    assert a in idx.supplemental_artifacts


def test_pr_url_is_supplemental():
    a = make_artifact(ArtifactType.PR_URL)
    idx = ArtifactNormalizer.index([a])
    assert a in idx.supplemental_artifacts


def test_branch_ref_is_supplemental():
    a = make_artifact(ArtifactType.BRANCH_REF)
    idx = ArtifactNormalizer.index([a])
    assert a in idx.supplemental_artifacts


# ---------------------------------------------------------------------------
# Mixed artifacts
# ---------------------------------------------------------------------------


def test_mixed_artifacts_split_correctly():
    diff = make_artifact(ArtifactType.DIFF, "diff")
    log = make_artifact(ArtifactType.LOG_EXCERPT, "log")
    val = make_artifact(ArtifactType.VALIDATION_REPORT, "val report")
    goal = make_artifact(ArtifactType.GOAL_FILE, "goal")

    idx = ArtifactNormalizer.index([diff, log, val, goal])
    assert diff in idx.primary_artifacts
    assert val in idx.primary_artifacts
    assert log in idx.supplemental_artifacts
    assert goal in idx.supplemental_artifacts
    assert len(idx.primary_artifacts) == 2
    assert len(idx.supplemental_artifacts) == 2


# ---------------------------------------------------------------------------
# Counts and type inventory
# ---------------------------------------------------------------------------


def test_artifact_counts_by_type():
    logs = [make_artifact(ArtifactType.LOG_EXCERPT, f"log {i}") for i in range(3)]
    diff = make_artifact(ArtifactType.DIFF)
    idx = ArtifactNormalizer.index([diff] + logs)
    assert idx.artifact_counts["log_excerpt"] == 3
    assert idx.artifact_counts["diff"] == 1


def test_artifact_types_present_sorted():
    arts = [
        make_artifact(ArtifactType.LOG_EXCERPT),
        make_artifact(ArtifactType.DIFF),
        make_artifact(ArtifactType.VALIDATION_REPORT),
    ]
    idx = ArtifactNormalizer.index(arts)
    assert idx.artifact_types_present == sorted(idx.artifact_types_present)
    assert "diff" in idx.artifact_types_present
    assert "log_excerpt" in idx.artifact_types_present
    assert "validation_report" in idx.artifact_types_present


def test_artifact_types_present_no_duplicates():
    arts = [
        make_artifact(ArtifactType.LOG_EXCERPT, "log 1"),
        make_artifact(ArtifactType.LOG_EXCERPT, "log 2"),
    ]
    idx = ArtifactNormalizer.index(arts)
    assert idx.artifact_types_present.count("log_excerpt") == 1


# ---------------------------------------------------------------------------
# is_primary helper
# ---------------------------------------------------------------------------


def test_is_primary_returns_true_for_diff():
    a = make_artifact(ArtifactType.DIFF)
    assert ArtifactNormalizer.is_primary(a) is True


def test_is_primary_returns_false_for_log():
    a = make_artifact(ArtifactType.LOG_EXCERPT)
    assert ArtifactNormalizer.is_primary(a) is False


# ---------------------------------------------------------------------------
# ArtifactIndex is frozen
# ---------------------------------------------------------------------------


def test_artifact_index_is_frozen():
    idx = ArtifactNormalizer.index([])
    with pytest.raises(Exception):
        idx.primary_artifacts = []  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Order preservation
# ---------------------------------------------------------------------------


def test_artifact_order_preserved():
    arts = [make_artifact(ArtifactType.DIFF, f"diff {i}") for i in range(5)]
    idx = ArtifactNormalizer.index(arts)
    labels = [a.label for a in idx.primary_artifacts]
    assert labels == ["diff 0", "diff 1", "diff 2", "diff 3", "diff 4"]
