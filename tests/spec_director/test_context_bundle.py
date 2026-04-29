# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
# tests/spec_director/test_context_bundle.py
from __future__ import annotations


def test_bundle_includes_seed():
    from operations_center.spec_director.context_bundle import ContextBundleBuilder
    builder = ContextBundleBuilder()
    bundle = builder.build(
        seed_text="add webhook ingestion",
        board_issues=[],
        specs_index=[],
        git_logs={},
        available_repos=[],
    )
    assert "add webhook ingestion" in bundle.seed_text


def test_specs_index_capped():
    from operations_center.spec_director.context_bundle import ContextBundleBuilder
    builder = ContextBundleBuilder()
    index = [{"title": f"spec {i}", "status": "complete"} for i in range(100)]
    bundle = builder.build(
        seed_text="",
        board_issues=[],
        specs_index=index,
        git_logs={},
        available_repos=[],
    )
    assert len(bundle.specs_index) <= 50
