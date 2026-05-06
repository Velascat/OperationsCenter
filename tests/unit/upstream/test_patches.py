# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Phase 14 R2 — patch records loader + validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.upstream.patches import (
    PatchError, PatchRegistry, UpstreamStatus, load_patches,
)


_VALID = """
id: PATCH-001
title: "Pass coach kwarg through orchestrator subclasses"
applied_at: "2026-05-05"
fork_branch: "fix/coach-kwarg-orchestrator-subclasses"
fork_dev_commit: "84a28f6"

contract_gap_ref: "kodo:G-004"

upstream:
  related_pr: "https://github.com/ikamensh/kodo/pull/49"
  upstream_status: pending_review

reconcile_when_any:
  - upstream_pr_merged: 49

touched_files:
  - kodo/orchestrators/claude_code.py

push_to_upstream:
  enabled: true
  pushed: false
"""


def _seed(tmp_path: Path, content: str = _VALID, fork: str = "kodo", name: str = "PATCH-001.yaml") -> Path:
    fork_dir = tmp_path / fork
    fork_dir.mkdir(parents=True, exist_ok=True)
    (fork_dir / name).write_text(content, encoding="utf-8")
    return tmp_path


class TestShippedPatch:
    def test_loads_real_kodo_patch_001(self):
        # Loads the actual shipped registry — not a test fixture
        reg = load_patches()
        kodo_patches = reg.for_fork("kodo")
        assert len(kodo_patches) == 1
        p = kodo_patches[0]
        assert p.id == "PATCH-001"
        assert p.contract_gap_ref == "kodo:G-004"
        assert p.upstream.upstream_status == UpstreamStatus.PENDING_REVIEW
        assert p.upstream.related_pr.endswith("/pull/49")
        assert p.push_to_upstream.enabled is True
        assert p.push_to_upstream.pushed is False
        assert "kodo/orchestrators/claude_code.py" in p.touched_files


class TestLoad:
    def test_loads_one_patch(self, tmp_path):
        reg = load_patches(_seed(tmp_path))
        kodo = reg.for_fork("kodo")
        assert len(kodo) == 1
        assert kodo[0].id == "PATCH-001"

    def test_returns_empty_on_missing_root(self, tmp_path):
        reg = load_patches(tmp_path / "absent")
        assert reg.by_fork == {}

    def test_get_by_full_id(self, tmp_path):
        reg = load_patches(_seed(tmp_path))
        p = reg.get("kodo:PATCH-001")
        assert p is not None and p.id == "PATCH-001"
        assert reg.get("nope:PATCH-001") is None
        assert reg.get("malformed-no-colon") is None


class TestValidation:
    def test_id_must_match_pattern(self, tmp_path):
        bad = _VALID.replace("id: PATCH-001", "id: not-a-patch-id")
        with pytest.raises(PatchError, match="PATCH-NNN"):
            load_patches(_seed(tmp_path, bad))

    def test_filename_must_match_id(self, tmp_path):
        with pytest.raises(PatchError, match="filename"):
            load_patches(_seed(tmp_path, _VALID, name="PATCH-002.yaml"))

    def test_missing_contract_gap_ref_rejected(self, tmp_path):
        bad = _VALID.replace('contract_gap_ref: "kodo:G-004"', "")
        with pytest.raises(PatchError, match="contract_gap_ref"):
            load_patches(_seed(tmp_path, bad))

    def test_invalid_contract_gap_ref_format(self, tmp_path):
        bad = _VALID.replace('"kodo:G-004"', '"badformat"')
        with pytest.raises(PatchError, match="contract_gap_ref"):
            load_patches(_seed(tmp_path, bad))

    def test_invalid_upstream_status_rejected(self, tmp_path):
        bad = _VALID.replace("upstream_status: pending_review", "upstream_status: weird_state")
        with pytest.raises(PatchError, match="upstream_status"):
            load_patches(_seed(tmp_path, bad))

    def test_touched_files_must_be_strings(self, tmp_path):
        bad = _VALID.replace(
            "  - kodo/orchestrators/claude_code.py",
            "  - 42",
        )
        with pytest.raises(PatchError, match="touched_files"):
            load_patches(_seed(tmp_path, bad))


class TestCrossReferenceWithCatalog:
    """Patches must reference real gaps; the contract_gap_ref ties into
    the executors/<fork>/contract_gaps.yaml entries."""

    def test_kodo_patch_001_references_real_gap(self):
        from operations_center.executors.startup import initialize_catalog
        cat = initialize_catalog(Path("src/operations_center/executors"))
        reg = load_patches()
        for patch in reg.all_patches():
            fork_id, gap_id = patch.contract_gap_ref.split(":", 1)
            entry = cat.entries.get(fork_id)
            assert entry is not None, (
                f"{patch.id}: contract_gap_ref {patch.contract_gap_ref} "
                f"references unknown executor {fork_id!r}"
            )
            gap_ids = {g.id for g in entry.contract_gaps}
            assert gap_id in gap_ids, (
                f"{patch.id}: gap {gap_id} not found in {fork_id}/contract_gaps.yaml"
            )
