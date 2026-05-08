# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Settings load — platform_manifest path resolution + slug auto-resolve."""
from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.config.settings import load_settings


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


def _write_config(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "operations_center.yaml"
    p.write_text(_BASE_YAML + body, encoding="utf-8")
    return p


class TestPathResolution:
    def test_absolute_paths_pass_through(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n"
            "  project_manifest_path: /etc/proj.yaml\n"
            "  local_manifest_path:   /etc/local.yaml\n",
        )
        s = load_settings(cfg)
        assert s.platform_manifest.project_manifest_path == Path("/etc/proj.yaml")
        assert s.platform_manifest.local_manifest_path == Path("/etc/local.yaml")

    def test_relative_paths_resolve_against_config_dir(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        cfg = config_dir / "operations_center.yaml"
        cfg.write_text(
            _BASE_YAML
            + "\nplatform_manifest:\n"
              "  project_manifest_path: ../project.yaml\n"
              "  local_manifest_path: local/here.yaml\n",
            encoding="utf-8",
        )
        (tmp_path / "project.yaml").touch()
        (config_dir / "local").mkdir()
        (config_dir / "local" / "here.yaml").touch()

        s = load_settings(cfg)
        assert s.platform_manifest.project_manifest_path == (tmp_path / "project.yaml").resolve()
        assert s.platform_manifest.local_manifest_path == (
            config_dir / "local" / "here.yaml"
        ).resolve()

    def test_tilde_expands_to_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n"
            "  project_manifest_path: ~/project.yaml\n",
        )
        s = load_settings(cfg)
        assert s.platform_manifest.project_manifest_path == tmp_path / "project.yaml"

    def test_none_paths_pass_through(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path, "")
        s = load_settings(cfg)
        assert s.platform_manifest.project_manifest_path is None
        assert s.platform_manifest.local_manifest_path is None


class TestSlugAutoResolve:
    def test_explicit_slug_wins(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nself_repo_key: ExampleManagedRepo\n"
            "platform_manifest:\n"
            "  project_slug: explicit-override\n",
        )
        s = load_settings(cfg)
        assert s.platform_manifest.project_slug == "explicit-override"

    def test_auto_slug_from_self_repo_key(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nself_repo_key: ExampleManagedRepo\n"
            "platform_manifest:\n  enabled: true\n",
        )
        s = load_settings(cfg)
        # The derivation lowercases without inserting separators between
        # camelCase boundaries — match the actual contract.
        assert s.platform_manifest.project_slug == "examplemanagedrepo"

    def test_auto_slug_translates_underscores(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nself_repo_key: my_project_repo\n"
            "platform_manifest:\n  enabled: true\n",
        )
        s = load_settings(cfg)
        assert s.platform_manifest.project_slug == "my-project-repo"

    def test_no_self_repo_key_leaves_slug_none(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n  enabled: true\n",
        )
        s = load_settings(cfg)
        assert s.platform_manifest.project_slug is None


class TestWorkScopeManifestPath:
    """v0.9.0+ — work_scope_manifest_path settings + XOR with project_manifest_path."""

    def test_work_scope_path_pass_through(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n"
            "  work_scope_manifest_path: ./work_scope.yaml\n",
        )
        s = load_settings(cfg)
        assert s.platform_manifest.work_scope_manifest_path == tmp_path / "work_scope.yaml"
        assert s.platform_manifest.project_manifest_path is None

    def test_both_paths_set_rejected(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n"
            "  project_manifest_path: ./project.yaml\n"
            "  work_scope_manifest_path: ./work_scope.yaml\n",
        )
        with pytest.raises(Exception, match="mutually exclusive"):
            load_settings(cfg)

    def test_work_scope_only_no_project(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\nplatform_manifest:\n"
            "  enabled: true\n"
            "  work_scope_manifest_path: ./scope.yaml\n",
        )
        s = load_settings(cfg)
        assert s.platform_manifest.work_scope_manifest_path is not None
        assert s.platform_manifest.project_manifest_path is None
