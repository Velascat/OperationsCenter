# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for fixture pack loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from operations_center.fixture_harvesting import (
    FixturePackLoadError,
    HarvestProfile,
    HarvestRequest,
    harvest_fixtures,
    load_fixture_pack,
)


def _harvest(index, profile: HarvestProfile, output_dir: Path, **kwargs):
    request = HarvestRequest(index=index, harvest_profile=profile, **kwargs)
    return harvest_fixtures(request, output_dir)


class TestLoadFixturePack:
    def test_load_by_json_path(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(completed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path)
        loaded = load_fixture_pack(pack_dir / "fixture_pack.json")
        assert loaded.fixture_pack_id == pack.fixture_pack_id

    def test_load_by_directory_path(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(completed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path)
        loaded = load_fixture_pack(pack_dir)
        assert loaded.fixture_pack_id == pack.fixture_pack_id

    def test_roundtrip_preserves_source_identity(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(completed_index, HarvestProfile.MINIMAL_FAILURE, tmp_path)
        loaded = load_fixture_pack(pack_dir)
        assert loaded.source_repo_id == pack.source_repo_id
        assert loaded.source_run_id == pack.source_run_id
        assert loaded.source_audit_type == pack.source_audit_type

    def test_roundtrip_preserves_artifacts(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path)
        loaded = load_fixture_pack(pack_dir)
        assert len(loaded.artifacts) == len(pack.artifacts)

    def test_roundtrip_preserves_profile(self, tmp_path: Path, completed_index) -> None:
        pack, pack_dir = _harvest(completed_index, HarvestProfile.PRODUCER_COMPLIANCE, tmp_path)
        loaded = load_fixture_pack(pack_dir)
        assert loaded.harvest_profile == HarvestProfile.PRODUCER_COMPLIANCE


class TestLoadErrors:
    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_fixture_pack(tmp_path / "nonexistent.json")

    def test_raises_for_invalid_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "fixture_pack.json"
        bad.write_text("not valid json", encoding="utf-8")
        with pytest.raises(FixturePackLoadError, match="not valid JSON"):
            load_fixture_pack(bad)

    def test_raises_for_invalid_schema(self, tmp_path: Path) -> None:
        bad = tmp_path / "fixture_pack.json"
        bad.write_text(json.dumps({"not": "a fixture pack"}), encoding="utf-8")
        with pytest.raises(FixturePackLoadError, match="schema validation"):
            load_fixture_pack(bad)

    def test_raises_for_missing_copied_artifact_file(
        self, tmp_path: Path, index_with_real_file
    ) -> None:
        pack, pack_dir = _harvest(
            index_with_real_file, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        # Delete a copied artifact file to simulate corruption
        for fa in pack.artifacts:
            if fa.copied and fa.fixture_relative_path:
                corrupt_target = pack_dir / "artifacts" / fa.fixture_relative_path
                if corrupt_target.exists():
                    corrupt_target.unlink()
                    break

        with pytest.raises(FixturePackLoadError, match="missing copied artifact"):
            load_fixture_pack(pack_dir)

    def test_metadata_only_artifacts_not_checked_for_file(
        self, tmp_path: Path, completed_index
    ) -> None:
        # completed_index has no resolved paths → all metadata-only
        pack, pack_dir = _harvest(
            completed_index, HarvestProfile.FULL_MANIFEST_SNAPSHOT, tmp_path
        )
        assert pack.metadata_only_count > 0
        # Loading should succeed — metadata-only artifacts don't require files
        loaded = load_fixture_pack(pack_dir)
        assert loaded is not None


class TestNoManagedRepoImports:
    def test_fixture_harvesting_does_not_import_managed_repo(self) -> None:
        import ast
        pkg_root = Path(__file__).resolve().parents[3] / "src" / "operations_center" / "fixture_harvesting"
        for py_file in pkg_root.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("tools.audit"), (
                        f"{py_file.name} imports managed repo code: {node.module}"
                    )

    def test_no_replay_test_functions_exist(self) -> None:
        import ast
        pkg_root = Path(__file__).resolve().parents[3] / "src" / "operations_center" / "fixture_harvesting"
        forbidden = frozenset({"run_replay", "execute_replay", "replay_test", "run_regression"})
        for py_file in pkg_root.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    assert node.name not in forbidden, (
                        f"{py_file.name} defines forbidden function {node.name!r} — "
                        "replay is Phase 10, not Phase 9"
                    )
