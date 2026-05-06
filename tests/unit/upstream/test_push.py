# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 R5 — auto-PR push tests (no real subprocess)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest
import yaml

from operations_center.upstream.push import (
    PushError, PushResult, drop_patch, push_patch,
)


_REGISTRY_PUSH_ON = """
forks:
  kodo:
    upstream:
      repo: ikamensh/kodo
    fork:
      repo: Velascat/kodo
      branch: dev
    base_commit: "90bdf8a"
    fork_commit: "84a28f6"
    install:
      kind: cli_tool
      modes:
        ci: "echo placeholder"
      local_clone_hint: __CLONE__
    poll_cadence_hours: 24
    auto_pr_push: true
"""

_REGISTRY_PUSH_OFF = _REGISTRY_PUSH_ON.replace("auto_pr_push: true", "auto_pr_push: false")

_PATCH_PUSHABLE = """
id: PATCH-001
title: "test patch"
applied_at: "2026-05-05"
fork_branch: "fix/x"
fork_dev_commit: "84a28f6"
contract_gap_ref: "kodo:G-004"
upstream:
  related_pr: "https://github.com/ikamensh/kodo/pull/49"
  upstream_status: pending_review
touched_files: []
push_to_upstream:
  enabled: true
  pushed: false
"""

_PATCH_ALREADY_PUSHED = _PATCH_PUSHABLE.replace(
    "  pushed: false",
    '  pushed: true\n  pushed_pr_url: "https://github.com/ikamensh/kodo/pull/100"',
)

_PATCH_PUSH_DISABLED = _PATCH_PUSHABLE.replace("  enabled: true", "  enabled: false")


def _seed(tmp_path: Path, *, registry: str = _REGISTRY_PUSH_ON, patch: str = _PATCH_PUSHABLE,
          monkeypatch=None) -> tuple[Path, Path, Path]:
    """Seed registry + patches + fake clone with a .git dir; return (reg, patches_root, clone)."""
    clone = tmp_path / "kodo"
    (clone / ".git").mkdir(parents=True)
    reg = tmp_path / "registry.yaml"
    reg.write_text(registry.replace("__CLONE__", str(clone)), encoding="utf-8")
    patches_root = tmp_path / "patches"
    (patches_root / "kodo").mkdir(parents=True)
    (patches_root / "kodo" / "PATCH-001.yaml").write_text(patch, encoding="utf-8")
    return reg, patches_root, clone


# ── Safety-rail validations ──────────────────────────────────────────────


class TestSafetyRails:
    def test_dry_run_does_not_invoke_subprocess(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        with mock_patch("operations_center.upstream.push.subprocess") as sp:
            result = push_patch("kodo:PATCH-001", registry_path=reg,
                                patches_root=patches_root, dry_run=True)
        assert result.ok
        assert result.detail.startswith("<dry-run>")
        sp.run.assert_not_called()

    def test_refuses_when_auto_pr_push_disabled(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path, registry=_REGISTRY_PUSH_OFF)
        with pytest.raises(PushError, match="auto_pr_push: false"):
            push_patch("kodo:PATCH-001", registry_path=reg, patches_root=patches_root)

    def test_refuses_when_patch_push_disabled(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path, patch=_PATCH_PUSH_DISABLED)
        with pytest.raises(PushError, match="enabled is false"):
            push_patch("kodo:PATCH-001", registry_path=reg, patches_root=patches_root)

    def test_refuses_when_already_pushed(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path, patch=_PATCH_ALREADY_PUSHED)
        with pytest.raises(PushError, match="already pushed"):
            push_patch("kodo:PATCH-001", registry_path=reg, patches_root=patches_root)

    def test_unknown_fork_raises(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        with pytest.raises(PushError, match="unknown fork"):
            push_patch("nope:PATCH-001", registry_path=reg, patches_root=patches_root)

    def test_unknown_patch_raises(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        with pytest.raises(PushError, match="not found"):
            push_patch("kodo:PATCH-999", registry_path=reg, patches_root=patches_root)

    def test_invalid_patch_id_format_raises(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        with pytest.raises(PushError, match="format"):
            push_patch("badformat", registry_path=reg, patches_root=patches_root)


# ── Subprocess interactions ──────────────────────────────────────────────


class TestPushFlow:
    def test_git_push_failure_recorded(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        with mock_patch("operations_center.upstream.push.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="permission denied",
            )
            result = push_patch("kodo:PATCH-001", registry_path=reg,
                                patches_root=patches_root)
        assert not result.ok
        assert not result.pushed_branch
        assert "permission denied" in result.detail

    def test_gh_pr_failure_recorded(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        # First call (git push) succeeds; second (gh pr create) fails
        with mock_patch("operations_center.upstream.push.subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
                subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="auth required"),
            ]
            result = push_patch("kodo:PATCH-001", registry_path=reg,
                                patches_root=patches_root)
        assert not result.ok
        assert result.pushed_branch
        assert not result.pr_created
        assert "auth required" in result.detail

    def test_full_success_records_pr_url_in_yaml(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        pr_url = "https://github.com/ikamensh/kodo/pull/123"
        with mock_patch("operations_center.upstream.push.subprocess.run") as run:
            run.side_effect = [
                subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
                subprocess.CompletedProcess(args=[], returncode=0, stdout=f"{pr_url}\n", stderr=""),
            ]
            result = push_patch("kodo:PATCH-001", registry_path=reg,
                                patches_root=patches_root)
        assert result.ok
        assert result.pr_url == pr_url

        # Yaml updated with pushed=true and pushed_pr_url
        loaded = yaml.safe_load((patches_root / "kodo" / "PATCH-001.yaml").read_text())
        assert loaded["push_to_upstream"]["pushed"] is True
        assert loaded["push_to_upstream"]["pushed_pr_url"] == pr_url


# ── Drop ──────────────────────────────────────────────────────────────


class TestDrop:
    def test_drop_removes_yaml(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        target = patches_root / "kodo" / "PATCH-001.yaml"
        assert target.exists()
        drop_patch("kodo:PATCH-001", patches_root=patches_root)
        assert not target.exists()

    def test_drop_unknown_patch_raises(self, tmp_path):
        reg, patches_root, _ = _seed(tmp_path)
        with pytest.raises(PushError, match="not found"):
            drop_patch("kodo:PATCH-999", patches_root=patches_root)
