# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 R1 — registry schema + loader tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.upstream.registry import (
    InstallKind,
    InstallMode,
    RegistryError,
    load_registry,
    resolve_local_clone,
)


_VALID = """
forks:
  kodo:
    upstream:
      repo: ikamensh/kodo
      latest_known_release: "0.4.272"
      latest_commit_sha: "90bdf8a"
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
      local_clone_hint: ~/Documents/GitHub/kodo
    poll_cadence_hours: 24
    auto_pr_push: true
"""


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "registry.yaml"
    p.write_text(content, encoding="utf-8")
    return p


class TestValidLoad:
    def test_loads_kodo_entry(self, tmp_path):
        reg = load_registry(_write(tmp_path, _VALID))
        kodo = reg.get("kodo")
        assert kodo.upstream.repo == "ikamensh/kodo"
        assert kodo.fork.repo == "Velascat/kodo"
        assert kodo.fork_commit == "84a28f6"
        assert kodo.install.kind == InstallKind.CLI_TOOL
        assert InstallMode.PROD in kodo.install.modes
        assert kodo.poll_cadence_hours == 24
        assert kodo.auto_pr_push is True

    def test_empty_registry_returns_empty(self, tmp_path):
        reg = load_registry(_write(tmp_path, "forks: {}\n"))
        assert reg.entries == {}

    def test_missing_file_returns_empty(self, tmp_path):
        reg = load_registry(tmp_path / "absent.yaml")
        assert reg.entries == {}

    def test_render_install_command_substitutes(self, tmp_path):
        reg = load_registry(_write(tmp_path, _VALID))
        kodo = reg.get("kodo")
        cmd = kodo.render_install_command(InstallMode.CI)
        assert "git+https://github.com/Velascat/kodo.git@84a28f6" in cmd

    def test_render_install_command_dev_requires_local_clone(self, tmp_path):
        reg = load_registry(_write(tmp_path, _VALID))
        kodo = reg.get("kodo")
        cmd = kodo.render_install_command(InstallMode.DEV, local_clone=Path("/tmp/clone"))
        assert "/tmp/clone" in cmd

    def test_render_install_dev_without_local_clone_raises(self, tmp_path):
        reg = load_registry(_write(tmp_path, _VALID))
        kodo = reg.get("kodo")
        with pytest.raises(RegistryError, match="local_clone"):
            kodo.render_install_command(InstallMode.DEV)


class TestValidationErrors:
    def test_missing_upstream_repo_rejected(self, tmp_path):
        bad = _VALID.replace("repo: ikamensh/kodo", "")
        with pytest.raises(RegistryError, match="upstream"):
            load_registry(_write(tmp_path, bad))

    def test_invalid_repo_format_rejected(self, tmp_path):
        bad = _VALID.replace("Velascat/kodo", "not-a-real-repo-format")
        with pytest.raises(RegistryError, match="invalid repo"):
            load_registry(_write(tmp_path, bad))

    def test_invalid_sha_rejected(self, tmp_path):
        bad = _VALID.replace("84a28f6", "Z!Q#nope")
        with pytest.raises(RegistryError, match="invalid commit SHA"):
            load_registry(_write(tmp_path, bad))

    def test_invalid_install_kind_rejected(self, tmp_path):
        bad = _VALID.replace("kind: cli_tool", "kind: vibes")
        with pytest.raises(RegistryError, match="invalid install.kind"):
            load_registry(_write(tmp_path, bad))

    def test_invalid_install_mode_rejected(self, tmp_path):
        bad = _VALID.replace("prod:", "wrong_mode:")
        with pytest.raises(RegistryError, match="invalid install mode"):
            load_registry(_write(tmp_path, bad))

    def test_invalid_fork_id_rejected(self, tmp_path):
        bad = _VALID.replace("kodo:", "Bad ID:")
        with pytest.raises(RegistryError, match="invalid fork_id"):
            load_registry(_write(tmp_path, bad))


class TestLocalCloneResolution:
    def test_resolves_via_clone_hint_when_git_dir_exists(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OC_UPSTREAM_CLONES_ROOT", raising=False)
        clone = tmp_path / "kodo"
        (clone / ".git").mkdir(parents=True)
        content = _VALID.replace(
            "local_clone_hint: ~/Documents/GitHub/kodo",
            f"local_clone_hint: {clone}",
        )
        reg = load_registry(_write(tmp_path, content))
        resolved = resolve_local_clone(reg.get("kodo"))
        assert resolved == clone

    def test_returns_none_when_no_clone_found(self, tmp_path, monkeypatch):
        # Isolate from the dev's actual home dir which may have Velascat/kodo
        monkeypatch.delenv("OC_UPSTREAM_CLONES_ROOT", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
        content = _VALID.replace(
            "local_clone_hint: ~/Documents/GitHub/kodo",
            "local_clone_hint: /tmp/definitely-nowhere",
        )
        reg = load_registry(_write(tmp_path, content))
        assert resolve_local_clone(reg.get("kodo")) is None

    def test_env_var_root_takes_precedence(self, tmp_path, monkeypatch):
        clone = tmp_path / "code" / "kodo"
        (clone / ".git").mkdir(parents=True)
        monkeypatch.setenv("OC_UPSTREAM_CLONES_ROOT", str(tmp_path / "code"))
        reg = load_registry(_write(tmp_path, _VALID))
        assert resolve_local_clone(reg.get("kodo")) == clone
