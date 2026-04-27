"""CLI tests for operations-center-fixtures commands.

Covers harvest / inspect / list using typer.testing.CliRunner.
Manifest loading and fixture harvesting are monkeypatched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from operations_center.entrypoints.fixtures.main import app
from operations_center.fixture_harvesting.models import HarvestProfile

_runner = CliRunner()

_LOAD_MANIFEST_TARGET = "operations_center.entrypoints.fixtures.main.load_artifact_manifest"
_BUILD_INDEX_TARGET = "operations_center.entrypoints.fixtures.main.build_artifact_index"
_HARVEST_TARGET = "operations_center.entrypoints.fixtures.main.harvest_fixtures"
_LOAD_PACK_TARGET = "operations_center.entrypoints.fixtures.main.load_fixture_pack"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_index() -> MagicMock:
    index = MagicMock()
    index.source.repo_id = "videofoundry"
    index.source.audit_type = "representative"
    index.source.run_id = "run_001"
    return index


def _make_mock_pack(
    fixture_pack_id: str = "pack_001",
    artifact_count: int = 2,
    copied_count: int = 2,
) -> MagicMock:
    pack = MagicMock()
    pack.fixture_pack_id = fixture_pack_id
    pack.source_repo_id = "videofoundry"
    pack.source_audit_type = "representative"
    pack.source_run_id = "run_001"
    pack.harvest_profile = HarvestProfile.MINIMAL_FAILURE
    pack.created_at = datetime.now(UTC)
    pack.artifact_count = artifact_count
    pack.copied_count = copied_count
    pack.metadata_only_count = 0
    pack.findings = []
    pack.limitations = []
    pack.artifacts = []
    return pack


def _make_manifest_file(tmp_path: Path) -> Path:
    p = tmp_path / "artifact_manifest.json"
    p.write_text("{}", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# cmd_harvest
# ---------------------------------------------------------------------------

class TestCmdHarvest:
    def test_harvest_success(self, tmp_path: Path):
        mf = _make_manifest_file(tmp_path)
        pack = _make_mock_pack()
        pack_dir = tmp_path / "packs" / "pack_001"
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=_make_mock_index()),
            patch(_HARVEST_TARGET, return_value=(pack, pack_dir)),
        ):
            out = _runner.invoke(
                app,
                ["harvest", "--manifest", str(mf), "--output-dir", str(tmp_path / "packs")],
            )
        assert out.exit_code == 0
        assert "pack_001" in out.output

    def test_harvest_input_error_exits_code_3(self, tmp_path: Path):
        from operations_center.fixture_harvesting import HarvestInputError
        mf = _make_manifest_file(tmp_path)
        with (
            patch(_LOAD_MANIFEST_TARGET, return_value=MagicMock()),
            patch(_BUILD_INDEX_TARGET, return_value=_make_mock_index()),
            patch(_HARVEST_TARGET, side_effect=HarvestInputError("bad input")),
        ):
            out = _runner.invoke(
                app,
                ["harvest", "--manifest", str(mf), "--output-dir", str(tmp_path)],
            )
        assert out.exit_code == 3
        assert "Harvest error" in out.output

    def test_harvest_manifest_not_found_exits_code_1(self, tmp_path: Path):
        from operations_center.artifact_index import ManifestNotFoundError
        with patch(_LOAD_MANIFEST_TARGET, side_effect=ManifestNotFoundError("missing")):
            out = _runner.invoke(
                app,
                ["harvest", "--manifest", "/no/such/manifest.json", "--output-dir", str(tmp_path)],
            )
        assert out.exit_code == 1
        assert "Not found" in out.output


# ---------------------------------------------------------------------------
# cmd_inspect
# ---------------------------------------------------------------------------

class TestCmdInspect:
    def test_inspect_success(self, tmp_path: Path):
        pack_file = tmp_path / "fixture_pack.json"
        pack_file.write_text("{}", encoding="utf-8")
        pack = _make_mock_pack()
        with patch(_LOAD_PACK_TARGET, return_value=pack):
            out = _runner.invoke(app, ["inspect", "--fixture-pack", str(pack_file)])
        assert out.exit_code == 0
        assert "pack_001" in out.output

    def test_inspect_not_found_exits_code_1(self, tmp_path: Path):
        with patch(_LOAD_PACK_TARGET, side_effect=FileNotFoundError("missing")):
            out = _runner.invoke(app, ["inspect", "--fixture-pack", "/no/such/pack.json"])
        assert out.exit_code == 1
        assert "Not found" in out.output

    def test_inspect_load_error_exits_code_2(self, tmp_path: Path):
        from operations_center.fixture_harvesting import FixturePackLoadError
        pack_file = tmp_path / "fixture_pack.json"
        pack_file.write_text("{}", encoding="utf-8")
        with patch(_LOAD_PACK_TARGET, side_effect=FixturePackLoadError("corrupted")):
            out = _runner.invoke(app, ["inspect", "--fixture-pack", str(pack_file)])
        assert out.exit_code == 2
        assert "Load error" in out.output


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

class TestCmdList:
    def test_list_no_packs(self, tmp_path: Path):
        out = _runner.invoke(app, ["list", "--root", str(tmp_path)])
        assert out.exit_code == 0
        assert "No fixture packs found" in out.output

    def test_list_missing_root_exits_ok(self, tmp_path: Path):
        out = _runner.invoke(app, ["list", "--root", str(tmp_path / "nonexistent")])
        assert out.exit_code == 0
        assert "Directory not found" in out.output

    def test_list_with_packs(self, tmp_path: Path):
        pack_dir = tmp_path / "pack_001"
        pack_dir.mkdir()
        pack_json = pack_dir / "fixture_pack.json"
        pack_json.write_text("{}", encoding="utf-8")
        pack = _make_mock_pack()
        with patch(_LOAD_PACK_TARGET, return_value=pack):
            out = _runner.invoke(app, ["list", "--root", str(tmp_path)])
        assert out.exit_code == 0
        assert "pack_001" in out.output
