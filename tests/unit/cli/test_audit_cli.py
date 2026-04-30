# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""CLI tests for operations-center-audit commands.

Covers run / status / resolve-manifest using typer.testing.CliRunner.
Dispatch is always monkeypatched — no real VideoFoundry subprocess is invoked.
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
        repo_id="videofoundry",
        audit_type="representative",
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
        "producer": "videofoundry",
        "repo_id": "videofoundry",
        "run_id": run_id,
        "audit_type": "representative",
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
            out = _runner.invoke(app, ["run", "--repo", "videofoundry", "--type", "representative"])
        assert out.exit_code == 0
        assert "run_001" in out.output

    def test_run_success_json_output(self):
        result = _make_dispatch_result(succeeded=True)
        with patch(_DISPATCH_TARGET, return_value=result):
            out = _runner.invoke(app, ["run", "--repo", "videofoundry", "--type", "representative", "--json"])
        assert out.exit_code == 0
        data = json.loads(out.output)
        assert data["run_id"] == "run_001"
        assert data["status"] == DispatchStatus.COMPLETED.value

    def test_run_failure_exits_code_1(self):
        result = _make_dispatch_result(succeeded=False)
        with patch(_DISPATCH_TARGET, return_value=result):
            out = _runner.invoke(app, ["run", "--repo", "videofoundry", "--type", "representative"])
        assert out.exit_code == 1

    def test_run_lock_conflict_exits_code_2(self):
        from operations_center.audit_dispatch import RepoLockAlreadyHeldError
        with patch(_DISPATCH_TARGET, side_effect=RepoLockAlreadyHeldError("locked")):
            out = _runner.invoke(app, ["run", "--repo", "videofoundry", "--type", "representative"])
        assert out.exit_code == 2
        assert "Lock conflict" in out.output

    def test_run_config_error_exits_code_3(self):
        from operations_center.audit_dispatch import AuditDispatchConfigError
        with patch(_DISPATCH_TARGET, side_effect=AuditDispatchConfigError("bad config")):
            out = _runner.invoke(app, ["run", "--repo", "videofoundry", "--type", "representative"])
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
