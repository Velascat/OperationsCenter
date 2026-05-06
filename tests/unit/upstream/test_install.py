# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 R1 — install + verify tests (no real subprocess)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.upstream import install as install_mod
from operations_center.upstream.install import (
    InstallError, VerifyStatus, install_fork, install_all, verify_install,
)
from operations_center.upstream.registry import (
    InstallMode, load_registry,
)


_VALID = """
forks:
  kodo:
    upstream:
      repo: ikamensh/kodo
      latest_known_release: "0.4.272"
    fork:
      repo: Velascat/kodo
      branch: dev
    base_commit: "90bdf8a"
    fork_commit: "84a28f6"
    install:
      kind: cli_tool
      modes:
        prod: "uv tool install --reinstall --force git+ssh://git@github.com/Velascat/kodo.git@{fork_commit}"
        ci:   "uv tool install --reinstall --force git+https://github.com/Velascat/kodo.git@{fork_commit}"
        dev:  "uv tool install --reinstall --force {local_clone}"
      local_clone_hint: /tmp/clone-not-real
"""


def _registry(tmp_path: Path) -> Path:
    p = tmp_path / "registry.yaml"
    p.write_text(_VALID, encoding="utf-8")
    return p


class TestInstall:
    def test_dry_run_renders_command_and_does_not_invoke(self, tmp_path, monkeypatch):
        # Register a fake clone so dev mode resolves
        clone = tmp_path / "kodo"
        (clone / ".git").mkdir(parents=True)
        monkeypatch.setenv("OC_UPSTREAM_CLONES_ROOT", str(tmp_path))
        reg = load_registry(_registry(tmp_path))
        result = install_fork(reg.get("kodo"), InstallMode.DEV, dry_run=True)
        assert result.ok
        assert str(clone) in result.command
        assert result.stdout == "<dry-run>"

    def test_dev_mode_without_local_clone_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OC_UPSTREAM_CLONES_ROOT", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        reg = load_registry(_registry(tmp_path))
        with pytest.raises(InstallError, match="local clone"):
            install_fork(reg.get("kodo"), InstallMode.DEV, dry_run=True)

    def test_ci_mode_renders_https_url(self, tmp_path):
        reg = load_registry(_registry(tmp_path))
        result = install_fork(reg.get("kodo"), InstallMode.CI, dry_run=True)
        assert "git+https://github.com/Velascat/kodo.git@84a28f6" in result.command

    def test_prod_mode_renders_ssh_url(self, tmp_path):
        reg = load_registry(_registry(tmp_path))
        result = install_fork(reg.get("kodo"), InstallMode.PROD, dry_run=True)
        assert "git+ssh://git@github.com/Velascat/kodo.git@84a28f6" in result.command


class TestVerify:
    def _seed_uv_tool_install(self, tmp_path: Path, *, sha: str, repo: str = "Velascat/kodo") -> Path:
        """Lay out a fake uv-tool install with a direct_url.json metadata file."""
        tool_dir = tmp_path / "tools" / "kodo"
        site_packages = tool_dir / "lib" / "python3.13" / "site-packages"
        dist_info = site_packages / "kodo-0.4.272.dist-info"
        dist_info.mkdir(parents=True)
        direct_url = {
            "url": f"git+https://github.com/{repo}.git@{sha}",
            "vcs_info": {"vcs": "git", "commit_id": sha, "requested_revision": sha},
        }
        (dist_info / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
        return tool_dir.parent  # uv-tool root

    def test_verify_ok_when_sha_matches(self, tmp_path, monkeypatch):
        uv_root = self._seed_uv_tool_install(tmp_path, sha="84a28f6abcdef")
        monkeypatch.setattr(install_mod, "_uv_tool_dir", lambda: uv_root)
        reg = load_registry(_registry(tmp_path))
        v = verify_install(reg.get("kodo"))
        assert v.status == VerifyStatus.OK

    def test_verify_wrong_sha(self, tmp_path, monkeypatch):
        uv_root = self._seed_uv_tool_install(tmp_path, sha="deadbeef1234")
        monkeypatch.setattr(install_mod, "_uv_tool_dir", lambda: uv_root)
        reg = load_registry(_registry(tmp_path))
        v = verify_install(reg.get("kodo"))
        assert v.status == VerifyStatus.WRONG_SHA

    def test_verify_wrong_repo(self, tmp_path, monkeypatch):
        # Same SHA but installed from upstream instead of fork
        uv_root = self._seed_uv_tool_install(tmp_path, sha="84a28f6", repo="ikamensh/kodo")
        monkeypatch.setattr(install_mod, "_uv_tool_dir", lambda: uv_root)
        reg = load_registry(_registry(tmp_path))
        v = verify_install(reg.get("kodo"))
        assert v.status == VerifyStatus.WRONG_REPO

    def test_verify_not_installed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(install_mod, "_uv_tool_dir", lambda: tmp_path / "empty-tools")
        reg = load_registry(_registry(tmp_path))
        v = verify_install(reg.get("kodo"))
        assert v.status == VerifyStatus.NOT_INSTALLED


class TestCLI:
    def test_install_dry_run_returns_zero(self, tmp_path, monkeypatch):
        clone = tmp_path / "kodo"
        (clone / ".git").mkdir(parents=True)
        monkeypatch.setenv("OC_UPSTREAM_CLONES_ROOT", str(tmp_path))
        from operations_center.upstream.cli import cmd_install
        code = cmd_install(
            fork_id="kodo", mode=InstallMode.DEV,
            all_forks=False, dry_run=True,
            registry_path=_registry(tmp_path),
        )
        assert code == 0

    def test_install_unknown_fork_returns_error(self, tmp_path):
        from operations_center.upstream.cli import cmd_install
        code = cmd_install(
            fork_id="nope", mode=InstallMode.CI,
            all_forks=False, dry_run=True,
            registry_path=_registry(tmp_path),
        )
        assert code == 2
