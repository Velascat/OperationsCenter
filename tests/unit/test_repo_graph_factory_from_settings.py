# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for build_effective_repo_graph_from_settings.

Settings-driven factory must:
- return None when disabled
- return platform-only graph when nothing else is configured
- attach a project layer when project_manifest_path is explicit
- attach a project layer via repo_root convention (topology/project_manifest.yaml)
- attach a local layer when local_manifest_path is explicit
- swallow malformed-manifest errors and return None (warning logged)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import operations_center.repo_graph_factory as repo_graph_factory
from operations_center.repo_graph_factory import (
    build_effective_repo_graph_from_settings,
)


class _PlatformManifestSettingsLike(BaseModel):
    enabled: bool = True
    project_slug: str | None = None
    project_manifest_path: Path | None = None
    work_scope_manifest_path: Path | None = None
    local_manifest_path: Path | None = None


class _SettingsLike(BaseModel):
    """Minimal Settings-shaped stub. The factory only reaches `.platform_manifest`."""
    platform_manifest: _PlatformManifestSettingsLike


def _settings(**pm: Any) -> _SettingsLike:
    return _SettingsLike(platform_manifest=_PlatformManifestSettingsLike(**pm))


_PROJECT_YAML = (
    'manifest_kind: project\n'
    'manifest_version: "1.0.0"\n'
    'repos:\n'
    '  myproj_api:\n'
    '    canonical_name: MyProjAPI\n'
    '    visibility: private\n'
    '    runtime_role: project_service\n'
    'edges:\n'
    '  - {from: MyProjAPI, to: OperationsCenter, type: dispatches_to}\n'
)

_LOCAL_YAML = (
    'manifest_kind: local\n'
    'manifest_version: "1.0.0"\n'
    'repos:\n'
    '  operations_center:\n'
    '    local_path: /opt/oc\n'
)


class TestDisabled:
    def test_disabled_returns_none(self) -> None:
        s = _settings(enabled=False)
        assert build_effective_repo_graph_from_settings(s) is None  # type: ignore[arg-type]


class TestPlatformOnly:
    def test_no_project_no_local_returns_platform_graph(self) -> None:
        s = _settings()
        g = build_effective_repo_graph_from_settings(s)  # type: ignore[arg-type]
        assert g is not None
        assert g.resolve("OperationsCenter") is not None
        assert all(n.repo_id != "myproj_api" for n in g.list_nodes())


class TestProjectExplicit:
    def test_explicit_project_manifest_path_loads(self, tmp_path: Path) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(_PROJECT_YAML, encoding="utf-8")
        s = _settings(project_manifest_path=proj)
        g = build_effective_repo_graph_from_settings(s)  # type: ignore[arg-type]
        assert g is not None
        assert g.resolve("MyProjAPI") is not None


class TestBaseOwnership:
    def test_factory_always_uses_bundled_platform_manifest_base(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(_PROJECT_YAML, encoding="utf-8")
        local = tmp_path / "local.yaml"
        local.write_text(_LOCAL_YAML, encoding="utf-8")
        recorded: dict[str, object] = {}

        def _fake_default_config_path() -> Path:
            return tmp_path / "bundled-platform.yaml"

        def _fake_load_effective_graph(base, *, project=None, work_scope=None, local=None):
            recorded["base"] = base
            recorded["project"] = project
            recorded["work_scope"] = work_scope
            recorded["local"] = local
            return "graph"

        monkeypatch.setattr(repo_graph_factory, "default_config_path", _fake_default_config_path)
        monkeypatch.setattr(repo_graph_factory, "load_effective_graph", _fake_load_effective_graph)

        graph = repo_graph_factory.build_effective_repo_graph(
            project_manifest_path=proj,
            local_manifest_path=local,
        )

        assert graph == "graph"
        assert recorded == {
            "base": tmp_path / "bundled-platform.yaml",
            "project": proj,
            "work_scope": None,
            "local": local,
        }


class TestProjectByRepoRootConvention:
    def test_topology_project_manifest_yaml_used_when_present(
        self, tmp_path: Path
    ) -> None:
        topology = tmp_path / "topology"
        topology.mkdir()
        (topology / "project_manifest.yaml").write_text(_PROJECT_YAML, encoding="utf-8")
        s = _settings()
        g = build_effective_repo_graph_from_settings(s, repo_root=tmp_path)  # type: ignore[arg-type]
        assert g is not None
        assert g.resolve("MyProjAPI") is not None

    def test_topology_path_absent_falls_back_to_platform_only(
        self, tmp_path: Path
    ) -> None:
        s = _settings()
        g = build_effective_repo_graph_from_settings(s, repo_root=tmp_path)  # type: ignore[arg-type]
        assert g is not None
        assert g.resolve("MyProjAPI") is None
        assert g.resolve("OperationsCenter") is not None


class TestLocalExplicit:
    def test_local_manifest_path_annotates(self, tmp_path: Path) -> None:
        local = tmp_path / "local.yaml"
        local.write_text(_LOCAL_YAML, encoding="utf-8")
        s = _settings(local_manifest_path=local)
        g = build_effective_repo_graph_from_settings(s)  # type: ignore[arg-type]
        assert g is not None
        oc = g.resolve("OperationsCenter")
        assert oc is not None
        assert oc.local_path == "/opt/oc"


class TestErrorSwallow:
    def test_malformed_project_manifest_returns_none_and_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  bad: {visibility: private}\n',
            encoding="utf-8",
        )
        s = _settings(project_manifest_path=proj)
        with caplog.at_level(logging.WARNING):
            g = build_effective_repo_graph_from_settings(s)  # type: ignore[arg-type]
        assert g is None
        assert any("graph construction failed" in rec.message.lower() for rec in caplog.records)

    def test_explicit_missing_project_path_returns_none(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        s = _settings(project_manifest_path=tmp_path / "does_not_exist.yaml")
        with caplog.at_level(logging.WARNING):
            g = build_effective_repo_graph_from_settings(s)  # type: ignore[arg-type]
        assert g is None


# ---------------------------------------------------------------------------
# v0.9.0+ — work-scope mode
# ---------------------------------------------------------------------------


_WORK_SCOPE_YAML_TEMPLATE = (
    'manifest_kind: work_scope\n'
    'manifest_version: "1.0.0"\n'
    'includes:\n'
    '  - {{name: MyProj, project_manifest_path: {project_path}}}\n'
)


class TestWorkScopeMode:
    def test_explicit_work_scope_path_loads(self, tmp_path: Path) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(_PROJECT_YAML, encoding="utf-8")
        ws = tmp_path / "work_scope.yaml"
        ws.write_text(
            _WORK_SCOPE_YAML_TEMPLATE.format(project_path=proj),
            encoding="utf-8",
        )
        s = _settings(work_scope_manifest_path=ws)
        g = build_effective_repo_graph_from_settings(s)  # type: ignore[arg-type]
        assert g is not None
        assert g.resolve("MyProjAPI") is not None
        assert g.resolve("OperationsCenter") is not None

    def test_work_scope_takes_precedence_over_topology_convention(
        self, tmp_path: Path
    ) -> None:
        # Even with a topology/project_manifest.yaml lying around,
        # explicit work_scope_manifest_path means the project topology
        # convention is not consulted.
        topology = tmp_path / "topology"
        topology.mkdir()
        (topology / "project_manifest.yaml").write_text(_PROJECT_YAML, encoding="utf-8")
        # Different project, included by the work scope:
        other_proj = tmp_path / "other_project.yaml"
        other_proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  other_api:\n'
            '    canonical_name: OtherAPI\n'
            '    visibility: private\n',
            encoding="utf-8",
        )
        ws = tmp_path / "work_scope.yaml"
        ws.write_text(
            _WORK_SCOPE_YAML_TEMPLATE.format(project_path=other_proj),
            encoding="utf-8",
        )
        s = _settings(work_scope_manifest_path=ws)
        g = build_effective_repo_graph_from_settings(s, repo_root=tmp_path)  # type: ignore[arg-type]
        assert g is not None
        # OtherAPI from the work scope is present; MyProjAPI from the
        # topology convention is NOT auto-loaded when work-scope mode wins.
        assert g.resolve("OtherAPI") is not None
        assert g.resolve("MyProjAPI") is None
