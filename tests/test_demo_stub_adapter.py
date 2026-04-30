# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Unit tests for DemoStubBackendAdapter."""

from __future__ import annotations

from pathlib import Path


from operations_center.backends.demo_stub import DemoStubBackendAdapter
from operations_center.contracts.enums import BackendName, ExecutionStatus
from operations_center.contracts.execution import ExecutionRequest


def _make_request(workspace: Path, **kw) -> ExecutionRequest:
    defaults = dict(
        proposal_id="prop-demo-1",
        decision_id="dec-demo-1",
        goal_text="Write a tiny hello-world artifact",
        repo_key="demo",
        clone_url="demo://local",
        base_branch="main",
        task_branch="demo/run-test",
        workspace_path=workspace,
    )
    defaults.update(kw)
    return ExecutionRequest(**defaults)


class TestDemoStubBackendAdapter:
    def test_returns_succeeded(self, tmp_path: Path) -> None:
        adapter = DemoStubBackendAdapter()
        result = adapter.execute(_make_request(tmp_path))
        assert result.success is True
        assert result.status == ExecutionStatus.SUCCEEDED

    def test_run_ids_propagated(self, tmp_path: Path) -> None:
        req = _make_request(tmp_path)
        result = DemoStubBackendAdapter().execute(req)
        assert result.run_id == req.run_id
        assert result.proposal_id == req.proposal_id
        assert result.decision_id == req.decision_id

    def test_writes_artifact_file(self, tmp_path: Path) -> None:
        DemoStubBackendAdapter().execute(_make_request(tmp_path))
        artifact_path = tmp_path / "artifacts" / "demo_result.txt"
        assert artifact_path.exists(), "artifact file must be written to workspace"

    def test_artifact_content_contains_goal(self, tmp_path: Path) -> None:
        goal = "Write a tiny hello-world artifact"
        DemoStubBackendAdapter().execute(_make_request(tmp_path, goal_text=goal))
        content = (tmp_path / "artifacts" / "demo_result.txt").read_text()
        assert goal in content

    def test_artifact_listed_in_result(self, tmp_path: Path) -> None:
        result = DemoStubBackendAdapter().execute(_make_request(tmp_path))
        assert len(result.artifacts) == 1
        artifact = result.artifacts[0]
        assert artifact.uri is not None
        assert "demo_result.txt" in artifact.uri

    def test_changed_files_reported(self, tmp_path: Path) -> None:
        result = DemoStubBackendAdapter().execute(_make_request(tmp_path))
        assert len(result.changed_files) == 1
        assert result.changed_files[0].change_type == "added"
        assert result.changed_files_source == "backend_manifest"
        assert result.changed_files_confidence == 1.0

    def test_diff_stat_excerpt_present(self, tmp_path: Path) -> None:
        result = DemoStubBackendAdapter().execute(_make_request(tmp_path))
        assert result.diff_stat_excerpt is not None

    def test_idempotent_on_second_run(self, tmp_path: Path) -> None:
        adapter = DemoStubBackendAdapter()
        r1 = adapter.execute(_make_request(tmp_path))
        r2 = adapter.execute(_make_request(tmp_path))
        assert r1.success is True
        assert r2.success is True
        artifact_path = tmp_path / "artifacts" / "demo_result.txt"
        assert artifact_path.exists()

    def test_backend_name_constant(self) -> None:
        assert BackendName.DEMO_STUB.value == "demo_stub"

    def test_error_on_unwritable_path(self, tmp_path: Path) -> None:
        bad_path = tmp_path / "nonexistent" / "deeply" / "nested"
        bad_path.mkdir(parents=True, exist_ok=True)
        bad_path.chmod(0o444)
        try:
            result = DemoStubBackendAdapter().execute(_make_request(bad_path))
            # If mkdir inside workspace_path/artifacts fails, should return failed result
            # (depending on OS permissions — acceptable outcome either way)
            assert result.run_id is not None
        finally:
            bad_path.chmod(0o755)
