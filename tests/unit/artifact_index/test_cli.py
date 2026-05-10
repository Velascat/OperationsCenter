# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CLI tests for Phase 7 index/index-show/get-artifact commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from operations_center.artifact_index.cli import app

_runner = CliRunner()


def _write_bucket(root: Path, *, audit_type: str, run_id: str, payload_factory, materialize: bool = False) -> Path:
    bucket = root / "tools" / "audit" / "report" / audit_type / f"Bucket_{run_id}"
    bucket.mkdir(parents=True, exist_ok=True)
    payload = payload_factory(run_id=run_id, run_root=str(bucket.relative_to(root)))
    payload["audit_type"] = audit_type
    (bucket / "artifact_manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    if materialize:
        for entry in payload["artifacts"]:
            if entry.get("location") in ("repo_singleton",):
                continue
            p = root / entry["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{}", encoding="utf-8")
    return bucket


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------


class TestCmdIndex:
    def test_empty_root_exits_2(self, tmp_path: Path) -> None:
        out = _runner.invoke(app, ["index", str(tmp_path)])
        assert out.exit_code == 2
        assert "No audit runs" in out.output

    def test_lists_runs_in_table(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        _write_bucket(tmp_path, audit_type="enrichment", run_id="run_b", payload_factory=_make_manifest_payload)
        out = _runner.invoke(app, ["index", str(tmp_path)])
        assert out.exit_code == 0
        assert "Audit Runs" in out.output

    def test_json_output(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        out = _runner.invoke(app, ["index", str(tmp_path), "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["runs"][0]["run_id"] == "run_a"

    def test_filter_by_repo(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        out = _runner.invoke(app, ["index", str(tmp_path), "--repo", "other", "--json"])
        # Filter eliminates everything → empty runs → exit 2
        assert out.exit_code == 2

    def test_load_error_appears_in_table(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken_bucket"
        bad.mkdir()
        (bad / "artifact_manifest.json").write_text("not json")
        out = _runner.invoke(app, ["index", str(tmp_path), "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["runs"][0]["load_error"] is not None


# ---------------------------------------------------------------------------
# index-show
# ---------------------------------------------------------------------------


class TestCmdIndexShow:
    def test_unknown_run_exits_1(self, tmp_path: Path) -> None:
        out = _runner.invoke(app, ["index-show", str(tmp_path), "missing"])
        assert out.exit_code == 1
        assert "Not found" in out.output

    def test_show_artifacts_table(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        out = _runner.invoke(app, ["index-show", str(tmp_path), "run_a"])
        assert out.exit_code == 0
        assert "run_a" in out.output

    def test_unique_prefix_works(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="abc12345", payload_factory=_make_manifest_payload)
        out = _runner.invoke(app, ["index-show", str(tmp_path), "abc"])
        assert out.exit_code == 0

    def test_ambiguous_prefix_exits_2(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="abc111", payload_factory=_make_manifest_payload)
        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="abc222", payload_factory=_make_manifest_payload)
        out = _runner.invoke(app, ["index-show", str(tmp_path), "abc"])
        assert out.exit_code == 2
        assert "Ambiguous" in out.output

    def test_failed_run_exits_3(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken_bucket"
        bad.mkdir()
        (bad / "artifact_manifest.json").write_text("not json")
        # Failed run has empty run_id; we pass empty prefix which is rejected.
        # Instead use a partial manifest that has a parseable run_id but invalid schema:
        partial = tmp_path / "partial_bucket"
        partial.mkdir()
        partial_payload = {"run_id": "broken_x", "schema_version": "WRONG"}
        (partial / "artifact_manifest.json").write_text(json.dumps(partial_payload))
        out = _runner.invoke(app, ["index-show", str(tmp_path), "broken_x"])
        assert out.exit_code == 3

    def test_json_output(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        out = _runner.invoke(app, ["index-show", str(tmp_path), "run_a", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["run"]["run_id"] == "run_a"
        assert "artifacts" in data


# ---------------------------------------------------------------------------
# get-artifact
# ---------------------------------------------------------------------------


class TestCmdGetArtifact:
    def test_resolves_path(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY  # type: ignore

        _write_bucket(
            tmp_path,
            audit_type="audit_type_1",
            run_id="run_a",
            payload_factory=_make_manifest_payload,
            materialize=True,
        )
        out = _runner.invoke(
            app,
            [
                "get-artifact",
                str(tmp_path),
                "run_a",
                _BASE_ENTRY["artifact_id"],
                "--repo-root",
                str(tmp_path),
            ],
        )
        assert out.exit_code == 0
        assert _BASE_ENTRY["path"] in out.output

    def test_missing_file_exits_5(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        out = _runner.invoke(
            app,
            ["get-artifact", str(tmp_path), "run_a", _BASE_ENTRY["artifact_id"], "--repo-root", str(tmp_path)],
        )
        assert out.exit_code == 5
        assert "missing" in out.output.lower()

    def test_no_recheck_returns_path_even_if_missing(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        out = _runner.invoke(
            app,
            [
                "get-artifact",
                str(tmp_path),
                "run_a",
                _BASE_ENTRY["artifact_id"],
                "--repo-root",
                str(tmp_path),
                "--no-recheck",
            ],
        )
        assert out.exit_code == 0

    def test_unknown_artifact_exits_1(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload  # type: ignore

        _write_bucket(tmp_path, audit_type="audit_type_1", run_id="run_a", payload_factory=_make_manifest_payload)
        out = _runner.invoke(
            app,
            ["get-artifact", str(tmp_path), "run_a", "no:such:artifact", "--repo-root", str(tmp_path)],
        )
        assert out.exit_code == 1

    def test_print_content(self, tmp_path: Path) -> None:
        from tests.unit.artifact_index.conftest import _make_manifest_payload, _BASE_ENTRY  # type: ignore

        _write_bucket(
            tmp_path,
            audit_type="audit_type_1",
            run_id="run_a",
            payload_factory=_make_manifest_payload,
            materialize=True,
        )
        # Override file content with something distinctive.
        (tmp_path / _BASE_ENTRY["path"]).write_text('{"hello": "world"}')
        out = _runner.invoke(
            app,
            [
                "get-artifact",
                str(tmp_path),
                "run_a",
                _BASE_ENTRY["artifact_id"],
                "--repo-root",
                str(tmp_path),
                "--print-content",
            ],
        )
        assert out.exit_code == 0
        assert "hello" in out.output
