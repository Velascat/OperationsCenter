# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for operations-center-graph-doctor (R4.1)."""
from __future__ import annotations

import json
from pathlib import Path


from operations_center.entrypoints.graph_doctor.main import main


_BASE_YAML = """\
plane:
  base_url: http://localhost:8080
  api_token_env: PLANE_API_TOKEN
  workspace_slug: ws
  project_id: pid

git:
  token_env: GH_TOKEN

kodo:
  binary: kodo

repos:
  Demo:
    clone_url: git@example.com:demo.git
    default_branch: main
"""


def _write_config(tmp_path: Path, body: str = "") -> Path:
    cfg = tmp_path / "operations_center.yaml"
    cfg.write_text(_BASE_YAML + body, encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestPlatformOnly:
    def test_default_settings_compose_clean(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(tmp_path)
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        report = json.loads(out)
        assert report["status"] == "ok"
        assert report["graph_built"] is True
        assert report["nodes_total"] >= 9  # bundled platform manifest has 9
        assert report["nodes_by_source"]["platform"] == report["nodes_total"]


class TestDisabled:
    def test_enabled_false_returns_ok_disabled(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n  enabled: false\n",
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "ok_disabled"
        assert report["graph_built"] is False


# ---------------------------------------------------------------------------
# Project layer
# ---------------------------------------------------------------------------


class TestWithProject:
    def test_project_layer_loaded(self, tmp_path: Path, capsys) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  vfa:\n'
            '    canonical_name: VFAApi\n'
            '    visibility: private\n'
            'edges:\n'
            '  - {from: VFAApi, to: OperationsCenter, type: dispatches_to}\n',
            encoding="utf-8",
        )
        cfg = _write_config(
            tmp_path,
            f"\nplatform_manifest:\n"
            f"  project_manifest_path: {proj}\n",
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "ok"
        assert report["nodes_by_source"].get("project") == 1
        assert report["edges_by_source"].get("project") == 1


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


class TestFailure:
    def test_missing_explicit_project_path_yields_warning_and_fail(
        self, tmp_path: Path, capsys
    ) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n"
            "  project_manifest_path: /does/not/exist.yaml\n",
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 1
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "fail_graph_none"
        assert report["graph_built"] is False
        assert any("manifest not found" in w.lower() for w in report["warnings"])

    def test_malformed_project_manifest_fails(self, tmp_path: Path, capsys) -> None:
        proj = tmp_path / "project.yaml"
        # Project manifest declares a node without canonical_name → loader error
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  bad: {visibility: private}\n',
            encoding="utf-8",
        )
        cfg = _write_config(
            tmp_path,
            f"\nplatform_manifest:\n"
            f"  project_manifest_path: {proj}\n",
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 1
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "fail_graph_none"
        assert any("graph construction failed" in w.lower() for w in report["warnings"])


# ---------------------------------------------------------------------------
# Invocation errors (exit 2)
# ---------------------------------------------------------------------------


class TestInvocationErrors:
    def test_missing_config_exits_two(self, tmp_path: Path, capsys) -> None:
        rc = main(["--config", str(tmp_path / "nope.yaml"), "--json"])
        assert rc == 2
        out = capsys.readouterr().out
        report = json.loads(out)
        assert report["status"] == "error"
        assert "config not found" in report["message"].lower()

    def test_malformed_config_exits_two(self, tmp_path: Path, capsys) -> None:
        cfg = tmp_path / "operations_center.yaml"
        cfg.write_text("not: [valid yaml here:", encoding="utf-8")
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 2
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "error"


# ---------------------------------------------------------------------------
# Human-readable output smoke
# ---------------------------------------------------------------------------


class TestHumanOutput:
    def test_human_default_ok(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(tmp_path)
        rc = main(["--config", str(cfg)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "✓ graph-doctor: ok" in out
        assert "graph_built:" in out
        assert "nodes_total:" in out

    def test_human_default_failure(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n"
            "  project_manifest_path: /does/not/exist.yaml\n",
        )
        rc = main(["--config", str(cfg)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "✗ graph-doctor: fail_graph_none" in out
        assert "warnings" in out


# ---------------------------------------------------------------------------
# v0.9.0+ mode reporting
# ---------------------------------------------------------------------------


_PROJECT_YAML = (
    'manifest_kind: project\n'
    'manifest_version: "1.0.0"\n'
    'repos:\n'
    '  myproj_api:\n'
    '    canonical_name: MyProjAPI\n'
    '    visibility: private\n'
    '    runtime_role: project_service\n'
)


class TestMode:
    def test_platform_only_mode(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(tmp_path)
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["platform_manifest"]["mode"] == "platform_only"

    def test_project_mode(self, tmp_path: Path, capsys) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(_PROJECT_YAML, encoding="utf-8")
        cfg = _write_config(
            tmp_path,
            f"\nplatform_manifest:\n  project_manifest_path: {proj}\n",
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["platform_manifest"]["mode"] == "project"
        assert report["platform_manifest"]["project_manifest_path"] == str(proj)

    def test_work_scope_mode(self, tmp_path: Path, capsys) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(_PROJECT_YAML, encoding="utf-8")
        ws = tmp_path / "work_scope.yaml"
        ws.write_text(
            'manifest_kind: work_scope\n'
            'manifest_version: "1.0.0"\n'
            'includes:\n'
            f'  - {{name: P, project_manifest_path: {proj}}}\n',
            encoding="utf-8",
        )
        cfg = _write_config(
            tmp_path,
            f"\nplatform_manifest:\n  work_scope_manifest_path: {ws}\n",
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["platform_manifest"]["mode"] == "work_scope"
        assert report["platform_manifest"]["work_scope_manifest_path"] == str(ws)
        assert report["graph_built"] is True
        assert report["nodes_by_source"]["project"] >= 1

    def test_disabled_mode(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(
            tmp_path, "\nplatform_manifest:\n  enabled: false\n"
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert report["platform_manifest"]["mode"] == "disabled"
