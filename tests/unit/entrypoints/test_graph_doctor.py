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


# ---------------------------------------------------------------------------
# Per-include breakdown (work-scope mode)
# ---------------------------------------------------------------------------


_PROJECT_A_YAML = (
    'manifest_kind: project\n'
    'manifest_version: "1.0.0"\n'
    'repos:\n'
    '  proj_a_api:\n'
    '    canonical_name: ProjectAAPI\n'
    '    visibility: private\n'
    'edges:\n'
    '  - {from: ProjectAAPI, to: OperationsCenter, type: dispatches_to}\n'
)
_PROJECT_B_YAML = (
    'manifest_kind: project\n'
    'manifest_version: "1.0.0"\n'
    'repos:\n'
    '  proj_b_api:\n'
    '    canonical_name: ProjectBAPI\n'
    '    visibility: private\n'
    '  proj_b_worker:\n'
    '    canonical_name: ProjectBWorker\n'
    '    visibility: private\n'
)


class TestPerIncludeBreakdown:
    def test_work_scope_mode_reports_per_include_counts(
        self, tmp_path: Path, capsys
    ) -> None:
        a = tmp_path / "a.yaml"
        a.write_text(_PROJECT_A_YAML, encoding="utf-8")
        b = tmp_path / "b.yaml"
        b.write_text(_PROJECT_B_YAML, encoding="utf-8")
        ws = tmp_path / "work_scope.yaml"
        ws.write_text(
            'manifest_kind: work_scope\n'
            'manifest_version: "1.0.0"\n'
            'includes:\n'
            f'  - {{name: A, project_manifest_path: {a}}}\n'
            f'  - {{name: B, project_manifest_path: {b}}}\n',
            encoding="utf-8",
        )
        cfg = _write_config(
            tmp_path, f"\nplatform_manifest:\n  work_scope_manifest_path: {ws}\n"
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        includes = report["includes"]
        assert len(includes) == 2
        names = {e["name"] for e in includes}
        assert names == {"A", "B"}
        a_entry = next(e for e in includes if e["name"] == "A")
        b_entry = next(e for e in includes if e["name"] == "B")
        # A contributes 1 node + 1 edge; B contributes 2 nodes, 0 edges
        assert a_entry["nodes_contributed"] == 1
        assert a_entry["edges_contributed"] == 1
        assert b_entry["nodes_contributed"] == 2
        assert b_entry["edges_contributed"] == 0

    def test_project_mode_has_no_includes_field(
        self, tmp_path: Path, capsys
    ) -> None:
        proj = tmp_path / "p.yaml"
        proj.write_text(_PROJECT_A_YAML, encoding="utf-8")
        cfg = _write_config(
            tmp_path, f"\nplatform_manifest:\n  project_manifest_path: {proj}\n"
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        assert "includes" not in report

    def test_human_output_lists_includes(self, tmp_path: Path, capsys) -> None:
        a = tmp_path / "a.yaml"
        a.write_text(_PROJECT_A_YAML, encoding="utf-8")
        ws = tmp_path / "work_scope.yaml"
        ws.write_text(
            'manifest_kind: work_scope\n'
            'manifest_version: "1.0.0"\n'
            'includes:\n'
            f'  - {{name: ProjectA, project_manifest_path: {a}}}\n',
            encoding="utf-8",
        )
        cfg = _write_config(
            tmp_path, f"\nplatform_manifest:\n  work_scope_manifest_path: {ws}\n"
        )
        rc = main(["--config", str(cfg)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "includes (1):" in out
        assert "ProjectA" in out
        assert "+1 nodes" in out


# ---------------------------------------------------------------------------
# Platform-manifest version + per-visibility counts (DoD #1, #2)
# ---------------------------------------------------------------------------


class TestPlatformManifestVersionReporting:
    def test_version_field_present_in_json(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(tmp_path)
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        # platform-manifest is an installed dependency; version should resolve
        assert "version" in report["platform_manifest"]
        version = report["platform_manifest"]["version"]
        assert version is not None
        # Looks like a PEP 440 version
        assert any(ch.isdigit() for ch in version)

    def test_version_in_human_output(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(tmp_path)
        rc = main(["--config", str(cfg)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "platform_manifest_version:" in out


class TestNodesByVisibility:
    def test_platform_only_reports_all_public(self, tmp_path: Path, capsys) -> None:
        cfg = _write_config(tmp_path)
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        nbv = report["nodes_by_visibility"]
        # Bundled platform manifest has only public nodes
        assert nbv["public"] == report["nodes_total"]
        assert nbv.get("private", 0) == 0

    def test_project_with_private_node_reports_private_count(
        self, tmp_path: Path, capsys
    ) -> None:
        proj = tmp_path / "p.yaml"
        proj.write_text(_PROJECT_YAML, encoding="utf-8")
        cfg = _write_config(
            tmp_path, f"\nplatform_manifest:\n  project_manifest_path: {proj}\n"
        )
        rc = main(["--config", str(cfg), "--json"])
        assert rc == 0
        report = json.loads(capsys.readouterr().out)
        nbv = report["nodes_by_visibility"]
        # _PROJECT_YAML adds one private node (myproj_api)
        assert nbv["private"] == 1
        assert nbv["public"] == report["nodes_total"] - 1

    def test_human_output_includes_visibility_breakdown(
        self, tmp_path: Path, capsys
    ) -> None:
        cfg = _write_config(tmp_path)
        rc = main(["--config", str(cfg)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "nodes_by_visibility:" in out
