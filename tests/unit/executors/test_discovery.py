# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Discovery harness tests — capture write + scrub pass-through."""
from __future__ import annotations

import json

from operations_center.executors.kodo.adapter import discover
from operations_center.executors._scrub import REDACTED


class TestKodoDiscovery:
    def test_capture_written_to_both_dirs(self, tmp_path):
        cap = discover(
            lane="coding_agent",
            invocation={"team": "_CLAUDE_FALLBACK_TEAM", "model_role": "worker_smart"},
            raw_output={"exit_code": 0, "stdout": "patch applied", "stderr": ""},
            duration_seconds=12.4,
            samples_base=tmp_path,
        )
        raw_files = list((tmp_path / "raw_output").glob("*.json"))
        inv_files = list((tmp_path / "invocations").glob("*.json"))
        assert len(raw_files) == 1
        assert len(inv_files) == 1
        assert raw_files[0].name == f"{cap.run_id}.json"
        assert inv_files[0].name == f"{cap.run_id}.json"

    def test_secrets_scrubbed_before_write(self, tmp_path):
        discover(
            lane="coding_agent",
            invocation={"api_key": "sk-realtoken1234567890abcdef", "endpoint": "https://x"},
            raw_output={"stdout": "see /home/alice/output.txt", "exit_code": 0},
            samples_base=tmp_path,
        )
        raw_text = (tmp_path / "raw_output").glob("*.json").__next__().read_text()
        inv_text = (tmp_path / "invocations").glob("*.json").__next__().read_text()
        assert "sk-realtoken" not in inv_text
        assert REDACTED in inv_text
        assert "/home/alice" not in raw_text
        assert "/<USER_HOME>" in raw_text

    def test_capture_carries_invocation_metadata(self, tmp_path):
        cap = discover(
            lane="planning",
            invocation={"backend": "kodo", "extra": "bar"},
            raw_output={"step_count": 3},
            samples_base=tmp_path,
        )
        inv = json.loads((tmp_path / "invocations" / f"{cap.run_id}.json").read_text())
        assert inv["lane"] == "planning"
        assert inv["invocation"]["backend"] == "kodo"


def test_archon_discovery_writes_to_archon_dir(tmp_path, monkeypatch):
    """Archon adapter delegates to the same primitives but lands under
    its own samples dir."""
    from operations_center.executors.archon import adapter as archon_adapter
    monkeypatch.setattr(archon_adapter, "_SAMPLES_BASE", tmp_path)

    # The archon discover() reads the module-level _SAMPLES_BASE
    cap = archon_adapter.discover(
        lane="workflow_agent",
        invocation={"workflow": "research_to_implementation"},
        raw_output={"workflow_events": [{"step": "plan"}, {"step": "execute"}]},
    )
    assert (tmp_path / "raw_output" / f"{cap.run_id}.json").exists()
    assert (tmp_path / "invocations" / f"{cap.run_id}.json").exists()
