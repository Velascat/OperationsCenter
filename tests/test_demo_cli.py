# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CLI smoke tests for the OperationsCenter demo entrypoint."""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.entrypoints.demo.run import main


class TestDemoCliSuccess:
    def test_exits_zero_on_happy_path(self, tmp_path: Path) -> None:
        rc = main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
            "--backend", "stub",
        ])
        assert rc == 0

    def test_demo_artifact_created(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
        ])
        artifact = tmp_path / "artifacts" / "demo_result.txt"
        assert artifact.exists(), "stub adapter must write demo_result.txt"

    def test_evidence_dir_created(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
        ])
        runs_dir = tmp_path / ".operations_center" / "runs"
        assert runs_dir.exists()
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1, "exactly one run directory expected"

    def test_evidence_files_all_present(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
        ])
        runs_dir = tmp_path / ".operations_center" / "runs"
        run_dir = next(runs_dir.iterdir())
        required = [
            "proposal.json",
            "decision.json",
            "execution_request.json",
            "result.json",
            "execution_record.json",
            "execution_trace.json",
            "run_metadata.json",
        ]
        for name in required:
            assert (run_dir / name).exists(), f"missing evidence file: {name}"

    def test_run_metadata_fields(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
        ])
        runs_dir = tmp_path / ".operations_center" / "runs"
        run_dir = next(runs_dir.iterdir())
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["selected_lane"] == "aider_local"
        assert meta["selected_backend"] == "demo_stub"
        assert meta["policy_status"] == "allow"
        assert meta["result_status"] == "succeeded"
        assert meta["success"] is True
        assert meta["executed"] is True

    def test_proposal_json_is_parseable(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
        ])
        runs_dir = tmp_path / ".operations_center" / "runs"
        run_dir = next(runs_dir.iterdir())
        payload = json.loads((run_dir / "proposal.json").read_text())
        assert payload["task_type"] == "simple_edit"
        assert payload["goal_text"] == "Write a tiny hello-world artifact"

    def test_result_json_shows_success(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
        ])
        runs_dir = tmp_path / ".operations_center" / "runs"
        run_dir = next(runs_dir.iterdir())
        result = json.loads((run_dir / "result.json").read_text())
        assert result["status"] == "succeeded"
        assert result["success"] is True


class TestDemoCliBlockedPolicy:
    def test_exits_nonzero_when_policy_blocks(self, tmp_path: Path) -> None:
        rc = main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
            "--blocked-policy",
        ])
        assert rc != 0

    def test_adapter_artifact_not_created_when_blocked(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
            "--blocked-policy",
        ])
        artifact = tmp_path / "artifacts" / "demo_result.txt"
        assert not artifact.exists(), "adapter must not run when policy blocks"

    def test_evidence_files_written_even_when_blocked(self, tmp_path: Path) -> None:
        """Observability record should be retained even for blocked runs."""
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
            "--blocked-policy",
        ])
        runs_dir = tmp_path / ".operations_center" / "runs"
        assert runs_dir.exists()
        run_dirs = list(runs_dir.iterdir())
        assert len(run_dirs) == 1
        run_dir = run_dirs[0]
        assert (run_dir / "result.json").exists()
        assert (run_dir / "execution_record.json").exists()

    def test_blocked_metadata_reflects_policy(self, tmp_path: Path) -> None:
        main([
            "--goal", "Write a tiny hello-world artifact",
            "--repo-key", "demo",
            "--workspace-path", str(tmp_path),
            "--blocked-policy",
        ])
        runs_dir = tmp_path / ".operations_center" / "runs"
        run_dir = next(runs_dir.iterdir())
        meta = json.loads((run_dir / "run_metadata.json").read_text())
        assert meta["executed"] is False
        assert meta["success"] is False
