# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the multi-run artifact index (Phase 7 Steps 1-5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.artifact_index.errors import (
    ArtifactNotFoundError,
    ArtifactPathUnresolvableError,
)
from operations_center.artifact_index.models import ArtifactQuery
from operations_center.artifact_index.multi_run import (
    MultiRunArtifactIndex,
    build_multi_run_index,
    discover_manifest_files,
)


def _write_bucket(
    root: Path,
    *,
    audit_type: str,
    run_id: str,
    payload_factory,
    bucket_name: str | None = None,
) -> Path:
    """Write one bucket dir under ``root`` with a manifest using payload_factory."""
    bucket = root / "tools" / "audit" / "report" / audit_type / (bucket_name or f"Bucket_{run_id}")
    bucket.mkdir(parents=True, exist_ok=True)
    payload = payload_factory(run_id=run_id, run_root=str(bucket.relative_to(root)))
    payload["audit_type"] = audit_type
    (bucket / "artifact_manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    return bucket


# ---------------------------------------------------------------------------
# Step 1: discover_manifest_files
# ---------------------------------------------------------------------------


class TestDiscoverManifestFiles:
    def test_returns_empty_for_empty_root(self, tmp_path: Path) -> None:
        assert discover_manifest_files(tmp_path) == []

    def test_returns_empty_for_missing_root(self, tmp_path: Path) -> None:
        assert discover_manifest_files(tmp_path / "nope") == []

    def test_finds_one_manifest(self, tmp_path: Path) -> None:
        bucket = tmp_path / "bucket"
        bucket.mkdir()
        m = bucket / "artifact_manifest.json"
        m.write_text("{}")
        assert discover_manifest_files(tmp_path) == [m]

    def test_finds_nested_manifests(self, tmp_path: Path, completed_manifest_payload) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        b1 = _write_bucket(tmp_path, audit_type="representative", run_id="r1", payload_factory=_make_manifest_payload)
        b2 = _write_bucket(tmp_path, audit_type="enrichment", run_id="r2", payload_factory=_make_manifest_payload)
        found = discover_manifest_files(tmp_path)
        assert len(found) == 2
        assert (b1 / "artifact_manifest.json") in found
        assert (b2 / "artifact_manifest.json") in found

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / "artifact_manifest.json").write_text("{}")
        assert discover_manifest_files(tmp_path) == []

    def test_skips_pycache(self, tmp_path: Path) -> None:
        pc = tmp_path / "__pycache__"
        pc.mkdir()
        (pc / "artifact_manifest.json").write_text("{}")
        assert discover_manifest_files(tmp_path) == []

    def test_depth_pruning(self, tmp_path: Path) -> None:
        # Manifest 3 levels deep — explicit max_depth=2 should miss it.
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "artifact_manifest.json").write_text("{}")
        assert discover_manifest_files(tmp_path, max_depth=2) == []
        # And again with deeper bound
        assert len(discover_manifest_files(tmp_path, max_depth=10)) == 1


# ---------------------------------------------------------------------------
# Step 2: build_multi_run_index happy path
# ---------------------------------------------------------------------------


class TestBuildHappyPath:
    def test_indexes_two_completed_runs(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="run_a", payload_factory=_make_manifest_payload)
        _write_bucket(tmp_path, audit_type="enrichment", run_id="run_b", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path)
        assert isinstance(idx, MultiRunArtifactIndex)
        assert len(idx.runs) == 2
        assert {r.run_id for r in idx.runs} == {"run_a", "run_b"}
        assert all(r.loaded for r in idx.runs)

    def test_run_metadata_populated(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="run_a", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path)
        run = idx.get_run("run_a")
        assert run is not None
        assert run.repo_id == "videofoundry"
        assert run.audit_type == "representative"
        assert run.producer == "videofoundry"
        assert run.artifact_count == 1
        assert run.is_partial is False

    def test_filter_by_repo(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="run_a", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path, repo_filter="other_repo")
        assert idx.runs == []

    def test_filter_by_audit_type(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="run_a", payload_factory=_make_manifest_payload)
        _write_bucket(tmp_path, audit_type="enrichment", run_id="run_b", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path, audit_type_filter="representative")
        assert {r.run_id for r in idx.runs} == {"run_a"}


# ---------------------------------------------------------------------------
# Step 3: failure handling
# ---------------------------------------------------------------------------


class TestFailureHandling:
    def test_corrupt_manifest_recorded_with_load_error(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="ok", payload_factory=_make_manifest_payload)
        # Plant a corrupt manifest in another bucket.
        bad = tmp_path / "tools" / "audit" / "report" / "render" / "broken"
        bad.mkdir(parents=True)
        (bad / "artifact_manifest.json").write_text("{not json")

        idx = build_multi_run_index(tmp_path)
        assert len(idx.runs) == 2
        failed = idx.failed_runs
        assert len(failed) == 1
        assert failed[0].load_error is not None
        assert failed[0].index is None
        # Loaded-only views.
        assert len(idx.loaded_runs) == 1

    def test_partial_manifest_indexes_normally(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        def partial_factory(*, run_id, run_root):
            return _make_manifest_payload(
                run_id=run_id,
                run_root=run_root,
                manifest_status="partial",
                run_status="interrupted",
                limitations=["partial_run"],
            )

        _write_bucket(tmp_path, audit_type="representative", run_id="r_partial", payload_factory=partial_factory)
        idx = build_multi_run_index(tmp_path)
        run = idx.get_run("r_partial")
        assert run is not None
        assert run.loaded
        assert run.is_partial is True


# ---------------------------------------------------------------------------
# Step 4: resolve with recheck_exists
# ---------------------------------------------------------------------------


class TestResolve:
    def test_resolve_returns_path_for_existing_artifact(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY  # type: ignore

        _bucket = _write_bucket(tmp_path, audit_type="representative", run_id="r1", payload_factory=_make_manifest_payload)
        # Materialize the artifact file so existence check passes.
        (tmp_path / _BASE_ENTRY["path"]).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / _BASE_ENTRY["path"]).write_text("{}")

        idx = build_multi_run_index(tmp_path, repo_root=tmp_path)
        path = idx.resolve("r1", _BASE_ENTRY["artifact_id"], recheck_exists=True)
        assert path.is_file()

    def test_resolve_raises_when_recheck_finds_missing_file(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="r1", payload_factory=_make_manifest_payload)
        # Don't create the artifact file — recheck should raise.
        idx = build_multi_run_index(tmp_path, repo_root=tmp_path)
        with pytest.raises(ArtifactPathUnresolvableError, match="no longer exists"):
            idx.resolve("r1", _BASE_ENTRY["artifact_id"], recheck_exists=True)

    def test_resolve_skips_recheck_when_disabled(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="r1", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path, repo_root=tmp_path)
        # Returns a path even though file is missing.
        path = idx.resolve("r1", _BASE_ENTRY["artifact_id"], recheck_exists=False)
        assert isinstance(path, Path)

    def test_resolve_unknown_run_raises(self, tmp_path: Path) -> None:
        idx = build_multi_run_index(tmp_path)
        with pytest.raises(ArtifactNotFoundError, match="no run"):
            idx.resolve("missing", "x")

    def test_resolve_failed_run_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken_bucket"
        bad.mkdir()
        (bad / "artifact_manifest.json").write_text("{not json")
        idx = build_multi_run_index(tmp_path)
        # Failed run has an empty run_id; lookup by manifest_path stem instead.
        run = idx.runs[0]
        with pytest.raises(ArtifactPathUnresolvableError, match="failed to load"):
            idx.resolve(run.run_id, "anything")


# ---------------------------------------------------------------------------
# Step 5: query federation + prefix lookup
# ---------------------------------------------------------------------------


class TestQueryFederation:
    def test_query_federates_across_runs(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY, _SINGLETON_ENTRY  # type: ignore

        def two_artifact_factory(*, run_id, run_root):
            return _make_manifest_payload(
                run_id=run_id,
                run_root=run_root,
                artifacts=[dict(_BASE_ENTRY), dict(_SINGLETON_ENTRY)],
            )

        _write_bucket(tmp_path, audit_type="representative", run_id="r1", payload_factory=two_artifact_factory)
        _write_bucket(tmp_path, audit_type="representative", run_id="r2", payload_factory=two_artifact_factory)

        idx = build_multi_run_index(tmp_path)
        all_artifacts = idx.query()
        # 2 entries × 2 runs = 4
        assert len(all_artifacts) == 4

    def test_query_with_filter(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY, _SINGLETON_ENTRY  # type: ignore

        def fac(*, run_id, run_root):
            return _make_manifest_payload(
                run_id=run_id,
                run_root=run_root,
                artifacts=[dict(_BASE_ENTRY), dict(_SINGLETON_ENTRY)],
            )

        _write_bucket(tmp_path, audit_type="representative", run_id="r1", payload_factory=fac)
        idx = build_multi_run_index(tmp_path)
        from operations_center.audit_contracts.vocabulary import Location

        singletons = idx.query(ArtifactQuery(location=Location.REPO_SINGLETON))
        assert len(singletons) == 1
        assert singletons[0].is_repo_singleton


class TestPrefixLookup:
    def test_exact_match(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="abc12345", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path)
        run = idx.find_run_by_prefix("abc12345")
        assert run.run_id == "abc12345"

    def test_unique_prefix(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="abc12345", payload_factory=_make_manifest_payload)
        _write_bucket(tmp_path, audit_type="representative", run_id="def67890", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path)
        assert idx.find_run_by_prefix("abc").run_id == "abc12345"

    def test_ambiguous_prefix_raises(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="representative", run_id="abc111", payload_factory=_make_manifest_payload)
        _write_bucket(tmp_path, audit_type="representative", run_id="abc222", payload_factory=_make_manifest_payload)
        idx = build_multi_run_index(tmp_path)
        with pytest.raises(ValueError, match="ambiguous"):
            idx.find_run_by_prefix("abc")

    def test_unknown_prefix_raises(self, tmp_path: Path) -> None:
        idx = build_multi_run_index(tmp_path)
        with pytest.raises(ArtifactNotFoundError):
            idx.find_run_by_prefix("xyz")

    def test_empty_prefix_raises(self, tmp_path: Path) -> None:
        idx = build_multi_run_index(tmp_path)
        with pytest.raises(ArtifactNotFoundError, match="empty"):
            idx.find_run_by_prefix("")
