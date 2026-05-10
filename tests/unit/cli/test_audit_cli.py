# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CLI tests for operations-center-audit commands.

Covers run / status / resolve-manifest using typer.testing.CliRunner.
Dispatch is always monkeypatched — no real ExampleManagedRepo subprocess is invoked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from operations_center.audit_dispatch.models import (
    DispatchStatus,
    FailureKind,
    ManagedAuditDispatchResult,
)
from operations_center.entrypoints.audit.main import app

_runner = CliRunner()

_DISPATCH_TARGET = "operations_center.entrypoints.audit.main.dispatch_managed_audit"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dispatch_result(succeeded: bool = True) -> ManagedAuditDispatchResult:
    now = datetime.now(UTC)
    return ManagedAuditDispatchResult(
        repo_id="example_managed_repo",
        audit_type="audit_type_1",
        run_id="run_001",
        status=DispatchStatus.COMPLETED if succeeded else DispatchStatus.FAILED,
        failure_kind=None if succeeded else FailureKind.PROCESS_NONZERO_EXIT,
        duration_seconds=1.5,
        started_at=now,
        ended_at=now,
        run_status_path="/tmp/run_status.json",
        artifact_manifest_path="/tmp/artifact_manifest.json",
        stdout_path="/tmp/stdout.log",
        stderr_path="/tmp/stderr.log",
    )


def _make_run_status(tmp_path: Path, run_id: str = "run_001", artifact_manifest_path: str | None = "artifacts/manifest.json") -> Path:
    data = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "example_managed_repo",
        "repo_id": "example_managed_repo",
        "run_id": run_id,
        "audit_type": "audit_type_1",
        "status": "completed",
        "artifact_manifest_path": artifact_manifest_path,
    }
    p = tmp_path / "run_status.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------

class TestCmdRun:
    def test_run_success_table_output(self):
        result = _make_dispatch_result(succeeded=True)
        with patch(_DISPATCH_TARGET, return_value=result):
            out = _runner.invoke(app, ["run", "--repo", "example_managed_repo", "--type", "audit_type_1"])
        assert out.exit_code == 0
        assert "run_001" in out.output

    def test_run_success_json_output(self):
        result = _make_dispatch_result(succeeded=True)
        with patch(_DISPATCH_TARGET, return_value=result):
            out = _runner.invoke(app, ["run", "--repo", "example_managed_repo", "--type", "audit_type_1", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["run_id"] == "run_001"
        assert data["status"] == DispatchStatus.COMPLETED.value

    def test_run_failure_exits_code_1(self):
        result = _make_dispatch_result(succeeded=False)
        with patch(_DISPATCH_TARGET, return_value=result):
            out = _runner.invoke(app, ["run", "--repo", "example_managed_repo", "--type", "audit_type_1"])
        assert out.exit_code == 1

    def test_run_lock_conflict_exits_code_2(self):
        from operations_center.audit_dispatch import RepoLockAlreadyHeldError
        with patch(_DISPATCH_TARGET, side_effect=RepoLockAlreadyHeldError("locked")):
            out = _runner.invoke(app, ["run", "--repo", "example_managed_repo", "--type", "audit_type_1"])
        assert out.exit_code == 2
        assert "Lock conflict" in out.output

    def test_run_config_error_exits_code_3(self):
        from operations_center.audit_dispatch import AuditDispatchConfigError
        with patch(_DISPATCH_TARGET, side_effect=AuditDispatchConfigError("bad config")):
            out = _runner.invoke(app, ["run", "--repo", "example_managed_repo", "--type", "audit_type_1"])
        assert out.exit_code == 3
        assert "Configuration error" in out.output


# ---------------------------------------------------------------------------
# cmd_status
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_status_table_output(self, tmp_path: Path):
        p = _make_run_status(tmp_path)
        out = _runner.invoke(app, ["status", str(p)])
        assert out.exit_code == 0
        assert "run_001" in out.output

    def test_status_json_output(self, tmp_path: Path):
        p = _make_run_status(tmp_path)
        out = _runner.invoke(app, ["status", str(p), "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["run_id"] == "run_001"

    def test_status_not_found_exits_code_1(self, tmp_path: Path):
        out = _runner.invoke(app, ["status", str(tmp_path / "missing.json")])
        assert out.exit_code == 1
        assert "Not found" in out.output


# ---------------------------------------------------------------------------
# cmd_resolve_manifest
# ---------------------------------------------------------------------------

class TestCmdResolveManifest:
    def test_resolve_manifest_prints_path(self, tmp_path: Path):
        manifest_file = tmp_path / "artifact_manifest.json"
        manifest_file.write_text("{}", encoding="utf-8")
        p = _make_run_status(tmp_path, artifact_manifest_path=str(manifest_file))
        out = _runner.invoke(app, ["resolve-manifest", str(p)])
        assert out.exit_code == 0
        assert str(manifest_file) in out.output

    def test_resolve_manifest_not_found_exits_code_1(self, tmp_path: Path):
        out = _runner.invoke(app, ["resolve-manifest", str(tmp_path / "missing.json")])
        assert out.exit_code == 1
        assert "Not found" in out.output


# ---------------------------------------------------------------------------
# cmd_list_active + cmd_unlock + cmd_dispatch (Slice D)
# ---------------------------------------------------------------------------


class TestCmdListActive:
    def test_list_active_empty(self, tmp_path: Path, monkeypatch):
        from operations_center.audit_dispatch.lock_store import PersistentLockStore

        monkeypatch.setattr(
            "operations_center.entrypoints.audit.main.get_global_registry",
            lambda: type("R", (), {"store": PersistentLockStore(tmp_path)})(),
        )
        out = _runner.invoke(app, ["list-active"])
        assert out.exit_code == 0
        assert "no active audit locks" in out.output

    def test_list_active_renders_table(self, tmp_path: Path, monkeypatch):
        import os

        from operations_center.audit_dispatch.lock_store import (
            PersistentLockPayload,
            PersistentLockStore,
        )

        store = PersistentLockStore(tmp_path)
        store.try_acquire(
            PersistentLockPayload(
                repo_id="example_managed_repo",
                run_id="vid_rep_xyz",
                audit_type="audit_type_1",
                oc_pid=os.getpid(),
                started_at="2026-05-04T12:00:00Z",
                command="python -m foo",
                expected_run_status_path="/tmp/output",
            )
        )
        monkeypatch.setattr(
            "operations_center.entrypoints.audit.main.get_global_registry",
            lambda: type("R", (), {"store": store})(),
        )
        out = _runner.invoke(app, ["list-active"])
        assert out.exit_code == 0
        # Rich may truncate long strings in narrow terminals; assert the table renders.
        assert "Active Audit Locks" in out.output

    def test_list_active_json(self, tmp_path: Path, monkeypatch):
        import os

        from operations_center.audit_dispatch.lock_store import (
            PersistentLockPayload,
            PersistentLockStore,
        )

        store = PersistentLockStore(tmp_path)
        store.try_acquire(
            PersistentLockPayload(
                repo_id="example_managed_repo",
                run_id="r1",
                audit_type="audit_type_1",
                oc_pid=os.getpid(),
                started_at="2026-05-04T12:00:00Z",
                command="x",
                expected_run_status_path="/tmp/x",
            )
        )
        monkeypatch.setattr(
            "operations_center.entrypoints.audit.main.get_global_registry",
            lambda: type("R", (), {"store": store})(),
        )
        out = _runner.invoke(app, ["list-active", "--json"])
        assert out.exit_code == 0
        rows = json.loads(out.output)
        assert rows[0]["repo_id"] == "example_managed_repo"
        assert rows[0]["oc_pid_alive"] is True


class TestCmdUnlock:
    def test_unlock_no_lock(self, tmp_path: Path, monkeypatch):
        from operations_center.audit_dispatch.lock_store import PersistentLockStore

        monkeypatch.setattr(
            "operations_center.entrypoints.audit.main.get_global_registry",
            lambda: type("R", (), {"store": PersistentLockStore(tmp_path)})(),
        )
        out = _runner.invoke(app, ["unlock", "--repo", "example_managed_repo"])
        assert out.exit_code == 0
        assert "No lock held" in out.output

    def test_unlock_refuses_alive(self, tmp_path: Path, monkeypatch):
        import os

        from operations_center.audit_dispatch.lock_store import (
            PersistentLockPayload,
            PersistentLockStore,
        )

        store = PersistentLockStore(tmp_path)
        store.try_acquire(
            PersistentLockPayload(
                repo_id="example_managed_repo",
                run_id="r1",
                audit_type="audit_type_1",
                oc_pid=os.getpid(),  # alive
                started_at="2026-05-04T12:00:00Z",
                command="x",
                expected_run_status_path="/tmp/x",
            )
        )
        monkeypatch.setattr(
            "operations_center.entrypoints.audit.main.get_global_registry",
            lambda: type("R", (), {"store": store})(),
        )
        out = _runner.invoke(app, ["unlock", "--repo", "example_managed_repo"])
        assert out.exit_code == 1
        assert "still alive" in out.output
        # Lock still exists.
        assert store.read("example_managed_repo") is not None

    def test_unlock_force_releases_alive(self, tmp_path: Path, monkeypatch):
        import os

        from operations_center.audit_dispatch.lock_store import (
            PersistentLockPayload,
            PersistentLockStore,
        )

        store = PersistentLockStore(tmp_path)
        store.try_acquire(
            PersistentLockPayload(
                repo_id="example_managed_repo",
                run_id="r1",
                audit_type="audit_type_1",
                oc_pid=os.getpid(),
                started_at="2026-05-04T12:00:00Z",
                command="x",
                expected_run_status_path="/tmp/x",
            )
        )
        monkeypatch.setattr(
            "operations_center.entrypoints.audit.main.get_global_registry",
            lambda: type("R", (), {"store": store})(),
        )
        out = _runner.invoke(app, ["unlock", "--repo", "example_managed_repo", "--force"])
        assert out.exit_code == 0
        assert "Force-released" in out.output
        assert store.read("example_managed_repo") is None

    def test_unlock_releases_stale(self, tmp_path: Path, monkeypatch):
        import subprocess
        import sys

        from operations_center.audit_dispatch.lock_store import (
            PersistentLockPayload,
            PersistentLockStore,
        )

        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        store = PersistentLockStore(tmp_path)
        store._write_atomic(
            tmp_path / "example_managed_repo.lock",
            PersistentLockPayload(
                repo_id="example_managed_repo",
                run_id="r1",
                audit_type="audit_type_1",
                oc_pid=proc.pid,  # dead
                started_at="2026-05-04T12:00:00Z",
                command="x",
                expected_run_status_path="/tmp/x",
            ),
        )
        monkeypatch.setattr(
            "operations_center.entrypoints.audit.main.get_global_registry",
            lambda: type("R", (), {"store": store})(),
        )
        out = _runner.invoke(app, ["unlock", "--repo", "example_managed_repo"])
        assert out.exit_code == 0
        assert "Released stale" in out.output
        assert store.read("example_managed_repo") is None


class TestCmdDispatch:
    def test_dispatch_alias_invokes_run(self, tmp_path: Path):
        with patch(_DISPATCH_TARGET, return_value=_make_dispatch_result()):
            out = _runner.invoke(app, ["dispatch", "example_managed_repo", "audit_type_1"])
        assert out.exit_code == 0
        assert "example_managed_repo" in out.output
