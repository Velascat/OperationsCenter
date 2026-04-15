# tests/spec_director/test_context_bundle.py
from __future__ import annotations
import json


def test_bundle_truncates_snapshot(tmp_path):
    from control_plane.spec_director.context_bundle import ContextBundleBuilder
    snapshot_dir = tmp_path / "report" / "autonomy_cycle" / "run1"
    snapshot_dir.mkdir(parents=True)
    big_insights = {"derivers": ["x" * 1000] * 20}
    (snapshot_dir / "insights.json").write_text(json.dumps(big_insights))
    builder = ContextBundleBuilder(report_root=tmp_path / "report", max_snapshot_kb=1)
    bundle = builder.build(seed_text="", board_summary=[], specs_index=[], git_log="")
    assert len(bundle.insight_snapshot.encode()) <= 1024 + 100  # small tolerance


def test_bundle_includes_seed():
    from control_plane.spec_director.context_bundle import ContextBundleBuilder
    builder = ContextBundleBuilder()
    bundle = builder.build(seed_text="add webhook ingestion", board_summary=[], specs_index=[], git_log="")
    assert "add webhook ingestion" in bundle.seed_text


def test_specs_index_capped():
    from control_plane.spec_director.context_bundle import ContextBundleBuilder
    builder = ContextBundleBuilder()
    index = [{"title": f"spec {i}", "status": "complete"} for i in range(100)]
    bundle = builder.build(seed_text="", board_summary=[], specs_index=index, git_log="")
    assert len(bundle.specs_index) <= 50
