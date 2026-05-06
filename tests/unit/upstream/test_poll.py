# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 R4 — poll + reconcile tests with a fake API client."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from operations_center.upstream.poll import (
    GhCliClient, PrSnapshot, ReconcileFinding, ReconcileSuggestion,
    UpstreamApiClient, UpstreamSnapshot, _extract_pr_number, poll_all, poll_fork,
    reconcile,
)
from operations_center.upstream.registry import load_registry
from operations_center.upstream.patches import load_patches


_REGISTRY = """
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
        ci: "uv tool install --reinstall --force git+https://github.com/Velascat/kodo.git@{fork_commit}"
    poll_cadence_hours: 24
    auto_pr_push: true
"""

_PATCH = """
id: PATCH-001
title: "test patch"
applied_at: "2026-05-05"
fork_branch: "fix/x"
fork_dev_commit: "84a28f6"
contract_gap_ref: "kodo:G-004"
upstream:
  related_pr: "https://github.com/ikamensh/kodo/pull/49"
  upstream_status: pending_review
reconcile_when_any:
  - upstream_pr_merged: 49
  - upstream_release_includes: "0.4.273"
touched_files:
  - kodo/orchestrators/claude_code.py
  - tests/orchestrators/test_orchestrator_signatures.py
push_to_upstream:
  enabled: true
  pushed: false
"""


def _seed(tmp_path: Path) -> tuple[Path, Path]:
    reg_path = tmp_path / "registry.yaml"
    reg_path.write_text(_REGISTRY, encoding="utf-8")
    patches_root = tmp_path / "patches"
    (patches_root / "kodo").mkdir(parents=True)
    (patches_root / "kodo" / "PATCH-001.yaml").write_text(_PATCH, encoding="utf-8")
    return reg_path, patches_root


class FakeClient:
    """Manually controllable UpstreamApiClient for tests."""

    def __init__(
        self,
        *,
        latest_release: str | None = None,
        latest_commit_sha: str | None = None,
        prs: dict[int, PrSnapshot] | None = None,
        files_changed: list[str] | None = None,
    ) -> None:
        self._release = latest_release
        self._commit = latest_commit_sha
        self._prs = prs or {}
        self._files = files_changed or []

    def latest_release(self, repo: str) -> str | None:
        return self._release

    def latest_commit_sha(self, repo: str, branch: str = "main") -> str | None:
        return self._commit

    def get_pr(self, repo: str, number: int) -> PrSnapshot | None:
        return self._prs.get(number)

    def files_changed_between(self, repo: str, base: str, head: str) -> list[str]:
        return list(self._files)


# ── Helpers ───────────────────────────────────────────────────────────


class TestExtractPRNumber:
    def test_from_url(self):
        assert _extract_pr_number("https://github.com/ikamensh/kodo/pull/49") == 49

    def test_from_url_with_query(self):
        assert _extract_pr_number("https://github.com/x/y/pull/123/files") == 123

    def test_from_bare_int(self):
        assert _extract_pr_number("49") == 49

    def test_unrecognized_returns_none(self):
        assert _extract_pr_number("not a url or number") is None


# ── Reconcile rules ───────────────────────────────────────────────────


class TestReconcile:
    def _patches_and_entry(self, tmp_path):
        reg_path, patches_root = _seed(tmp_path)
        registry = load_registry(reg_path)
        patches = load_patches(patches_root).for_fork("kodo")
        return registry.get("kodo"), patches

    def test_drop_patch_when_pr_merged(self, tmp_path):
        entry, patches = self._patches_and_entry(tmp_path)
        snap = UpstreamSnapshot(
            fork_id="kodo", upstream_repo=entry.upstream.repo,
            cited_prs={49: PrSnapshot(number=49, state="closed", merged=True,
                                       last_activity_iso="2026-06-12")},
        )
        findings = reconcile(entry, patches, snap, today=date(2026, 6, 12))
        assert any(f.suggestion == ReconcileSuggestion.DROP_PATCH for f in findings)

    def test_drop_patch_when_release_includes_target(self, tmp_path):
        entry, patches = self._patches_and_entry(tmp_path)
        snap = UpstreamSnapshot(
            fork_id="kodo", upstream_repo=entry.upstream.repo,
            latest_release="0.4.273",
        )
        findings = reconcile(entry, patches, snap, today=date(2026, 6, 12))
        assert any(
            f.suggestion == ReconcileSuggestion.DROP_PATCH and "0.4.273" in f.reason
            for f in findings
        )

    def test_rebase_patch_when_files_changed(self, tmp_path):
        entry, patches = self._patches_and_entry(tmp_path)
        snap = UpstreamSnapshot(
            fork_id="kodo", upstream_repo=entry.upstream.repo,
            files_changed_since_base=["kodo/orchestrators/claude_code.py", "unrelated.py"],
        )
        findings = reconcile(entry, patches, snap)
        rebases = [f for f in findings if f.suggestion == ReconcileSuggestion.REBASE_PATCH]
        assert len(rebases) == 1
        assert "claude_code.py" in rebases[0].reason

    def test_push_patch_when_unpushed_and_auto_enabled(self, tmp_path):
        entry, patches = self._patches_and_entry(tmp_path)
        snap = UpstreamSnapshot(fork_id="kodo", upstream_repo=entry.upstream.repo)
        findings = reconcile(entry, patches, snap)
        assert any(f.suggestion == ReconcileSuggestion.PUSH_PATCH for f in findings)

    def test_no_findings_when_all_quiet(self, tmp_path):
        # auto_pr_push=False fork — disable push suggestion
        reg_path, patches_root = _seed(tmp_path)
        # Disable auto_pr_push to silence PUSH_PATCH
        text = reg_path.read_text()
        reg_path.write_text(text.replace("auto_pr_push: true", "auto_pr_push: false"))
        registry = load_registry(reg_path)
        patches = load_patches(patches_root).for_fork("kodo")
        entry = registry.get("kodo")
        snap = UpstreamSnapshot(fork_id="kodo", upstream_repo=entry.upstream.repo)
        findings = reconcile(entry, patches, snap)
        assert findings == []

    def test_stale_review_when_pr_open_and_idle(self, tmp_path):
        # Patch with pushed_pr_url, in pushed_prs map
        reg_path, patches_root = _seed(tmp_path)
        # Mark patch as pushed with a stale PR URL
        patch_path = patches_root / "kodo" / "PATCH-001.yaml"
        patch_path.write_text(
            patch_path.read_text().replace(
                "  pushed: false",
                '  pushed: true\n  pushed_pr_url: "https://github.com/ikamensh/kodo/pull/99"',
            )
        )
        registry = load_registry(reg_path)
        patches = load_patches(patches_root).for_fork("kodo")
        entry = registry.get("kodo")
        snap = UpstreamSnapshot(
            fork_id="kodo", upstream_repo=entry.upstream.repo,
            pushed_prs={"PATCH-001": PrSnapshot(
                number=99, state="open", merged=False,
                last_activity_iso=(date.today() - timedelta(days=45)).isoformat(),
            )},
        )
        findings = reconcile(entry, patches, snap, today=date.today())
        assert any(f.suggestion == ReconcileSuggestion.STALE_REVIEW for f in findings)

    def test_review_request_abandoned_when_pr_closed_unmerged(self, tmp_path):
        reg_path, patches_root = _seed(tmp_path)
        patch_path = patches_root / "kodo" / "PATCH-001.yaml"
        patch_path.write_text(
            patch_path.read_text().replace(
                "  pushed: false",
                '  pushed: true\n  pushed_pr_url: "https://github.com/ikamensh/kodo/pull/99"',
            )
        )
        registry = load_registry(reg_path)
        patches = load_patches(patches_root).for_fork("kodo")
        entry = registry.get("kodo")
        snap = UpstreamSnapshot(
            fork_id="kodo", upstream_repo=entry.upstream.repo,
            pushed_prs={"PATCH-001": PrSnapshot(
                number=99, state="closed", merged=False, last_activity_iso="2026-06-01",
            )},
        )
        findings = reconcile(entry, patches, snap, today=date(2026, 7, 1))
        assert any(
            f.suggestion == ReconcileSuggestion.REVIEW_REQUEST_ABANDONED for f in findings
        )


# ── End-to-end poll_all ───────────────────────────────────────────────


class TestPollAll:
    def test_poll_all_with_fake_client_returns_findings(self, tmp_path):
        reg_path, patches_root = _seed(tmp_path)
        client = FakeClient(
            latest_release="0.4.273",
            latest_commit_sha="abc1234",
            prs={49: PrSnapshot(number=49, state="closed", merged=True,
                                last_activity_iso="2026-06-12")},
        )
        findings = poll_all(
            registry_path=reg_path, patches_root=patches_root,
            client=client, today=date(2026, 6, 12),
        )
        assert any(f.suggestion == ReconcileSuggestion.DROP_PATCH for f in findings)
        # fork_id:PATCH-NNN format preserved
        assert all(f.patch_id.startswith("kodo:") for f in findings)


# ── CLI ───────────────────────────────────────────────────────────────


class TestPollCLI:
    def test_returns_zero_when_no_findings(self, tmp_path, monkeypatch):
        # Empty registry → no findings → exit 0
        empty = tmp_path / "registry.yaml"
        empty.write_text("forks: {}\n", encoding="utf-8")
        from operations_center.upstream.cli import cmd_poll
        # Needs to not call out to gh; default GhCliClient is fine since
        # it returns None on missing gh command
        code = cmd_poll(registry_path=empty, json_output=True)
        assert code == 0
