"""CLI tests for operations-center-governance commands.

Covers the full request → evaluate → approve → run round-trip using
typer.testing.CliRunner. Dispatch is always monkeypatched — no real
VideoFoundry subprocess is invoked.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from operations_center.audit_dispatch.models import DispatchStatus, ManagedAuditDispatchResult
from operations_center.audit_governance import (
    AuditGovernanceDecision,
    AuditGovernanceRequest,
    GovernanceConfig,
    evaluate_governance_policies,
    make_governance_decision,
    make_manual_approval,
)
from operations_center.entrypoints.governance.main import app

_runner = CliRunner()
_DISPATCH_TARGET = "operations_center.audit_governance.runner.dispatch_managed_audit"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request_file(tmp_path: Path, **kwargs) -> Path:
    defaults = dict(
        repo_id="videofoundry",
        audit_type="representative",
        requested_by="alice",
        requested_reason="fixture refresh",
        urgency="normal",
        related_suite_report_path="/suite.json",
    )
    defaults.update(kwargs)
    req = AuditGovernanceRequest(**defaults)
    p = tmp_path / "request.json"
    p.write_text(req.model_dump_json(indent=2), encoding="utf-8")
    return p


def _make_decision_file(tmp_path: Path, request: AuditGovernanceRequest, **eval_kwargs) -> tuple[Path, AuditGovernanceDecision]:
    cfg = GovernanceConfig(
        known_repos=eval_kwargs.get("known_repos", ["videofoundry"]),
        known_audit_types=eval_kwargs.get("known_audit_types", {"videofoundry": ["representative"]}),
    )
    results = evaluate_governance_policies(
        request,
        known_repos=cfg.known_repos,
        known_audit_types=cfg.known_audit_types,
    )
    decision = make_governance_decision(request, results)
    p = tmp_path / "decision.json"
    p.write_text(decision.model_dump_json(indent=2), encoding="utf-8")
    return p, decision


def _make_dispatch_result() -> ManagedAuditDispatchResult:
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    return ManagedAuditDispatchResult(
        repo_id="videofoundry",
        audit_type="representative",
        run_id="cli_test_run_001",
        status=DispatchStatus.COMPLETED,
        started_at=now,
        ended_at=now,
        duration_seconds=1.0,
    )


# ---------------------------------------------------------------------------
# cmd_request
# ---------------------------------------------------------------------------

class TestCmdRequest:
    def test_prints_request_json_to_stdout(self):
        result = _runner.invoke(app, [
            "request",
            "--repo", "videofoundry",
            "--type", "representative",
            "--reason", "manual fixture refresh",
            "--requested-by", "alice",
        ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["repo_id"] == "videofoundry"
        assert data["audit_type"] == "representative"

    def test_writes_request_to_file(self, tmp_path: Path):
        out = tmp_path / "req.json"
        result = _runner.invoke(app, [
            "request",
            "--repo", "videofoundry",
            "--type", "representative",
            "--reason", "manual refresh",
            "--requested-by", "alice",
            "--output", str(out),
        ])
        assert result.exit_code == 0
        assert out.exists()
        data = json.loads(out.read_text())
        assert data["repo_id"] == "videofoundry"

    def test_invalid_urgency_exits_nonzero(self):
        result = _runner.invoke(app, [
            "request",
            "--repo", "videofoundry",
            "--type", "representative",
            "--reason", "test",
            "--requested-by", "alice",
            "--urgency", "invalid_urgency_value",
        ])
        assert result.exit_code != 0

    def test_missing_required_flags_exits_nonzero(self):
        result = _runner.invoke(app, ["request", "--repo", "videofoundry"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# cmd_evaluate
# ---------------------------------------------------------------------------

class TestCmdEvaluate:
    def test_evaluate_known_repo_exits_zero(self, tmp_path: Path):
        req_path = _make_request_file(tmp_path)
        result = _runner.invoke(app, [
            "evaluate",
            "--request", str(req_path),
            "--known-repos", "videofoundry",
            "--known-types", "representative",
        ])
        assert result.exit_code == 0

    def test_evaluate_unknown_repo_exits_nonzero(self, tmp_path: Path):
        req_path = _make_request_file(tmp_path, repo_id="unknown_repo")
        result = _runner.invoke(app, [
            "evaluate",
            "--request", str(req_path),
            "--known-repos", "videofoundry",
            "--known-types", "representative",
        ])
        assert result.exit_code != 0

    def test_evaluate_missing_request_file_exits_nonzero(self, tmp_path: Path):
        result = _runner.invoke(app, [
            "evaluate",
            "--request", str(tmp_path / "nonexistent.json"),
        ])
        assert result.exit_code != 0

    def test_evaluate_prints_decision(self, tmp_path: Path):
        req_path = _make_request_file(tmp_path)
        result = _runner.invoke(app, [
            "evaluate",
            "--request", str(req_path),
            "--known-repos", "videofoundry",
            "--known-types", "representative",
        ])
        assert "APPROVED" in result.output.upper() or "NEEDS_MANUAL_APPROVAL" in result.output.upper()


# ---------------------------------------------------------------------------
# cmd_approve
# ---------------------------------------------------------------------------

class TestCmdApprove:
    def test_approve_needs_manual_approval_decision(self, tmp_path: Path):
        req = AuditGovernanceRequest(
            repo_id="videofoundry",
            audit_type="representative",
            requested_by="alice",
            requested_reason="test",
            urgency="urgent",
            related_suite_report_path="/suite.json",
        )
        req_path = tmp_path / "req.json"
        req_path.write_text(req.model_dump_json(indent=2), encoding="utf-8")
        dec_path, decision = _make_decision_file(tmp_path, req)
        assert decision.requires_manual_approval

        out = tmp_path / "approval.json"
        result = _runner.invoke(app, [
            "approve",
            "--decision", str(dec_path),
            "--request", str(req_path),
            "--approved-by", "bob",
            "--output", str(out),
        ])
        assert result.exit_code == 0
        assert out.exists()
        approval_data = json.loads(out.read_text())
        assert approval_data["approved_by"] == "bob"
        assert approval_data["request_id"] == req.request_id

    def test_approve_missing_decision_file(self, tmp_path: Path):
        req_path = _make_request_file(tmp_path)
        result = _runner.invoke(app, [
            "approve",
            "--decision", str(tmp_path / "missing.json"),
            "--request", str(req_path),
            "--approved-by", "bob",
        ])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------

class TestCmdRun:
    def test_run_approved_dispatches(self, tmp_path: Path):
        req_path = _make_request_file(tmp_path)
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())
        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = _runner.invoke(app, [
                "run",
                "--request", str(req_path),
                "--known-repos", "videofoundry",
                "--known-types", "representative",
                "--output-dir", str(tmp_path / "out"),
                "--state-dir", str(tmp_path / "state"),
            ])
        assert dispatch_mock.called, f"Dispatch not called. Output: {result.output}"
        assert result.exit_code == 0

    def test_run_denied_exits_nonzero(self, tmp_path: Path):
        req_path = _make_request_file(tmp_path, repo_id="unknown_repo")
        result = _runner.invoke(app, [
            "run",
            "--request", str(req_path),
            "--known-repos", "videofoundry",
            "--known-types", "representative",
            "--output-dir", str(tmp_path / "out"),
            "--state-dir", str(tmp_path / "state"),
        ])
        assert result.exit_code != 0

    def test_run_needs_manual_approval_without_approval_exits_nonzero(self, tmp_path: Path):
        req_path = _make_request_file(tmp_path, urgency="urgent")
        result = _runner.invoke(app, [
            "run",
            "--request", str(req_path),
            "--known-repos", "videofoundry",
            "--known-types", "representative",
            "--output-dir", str(tmp_path / "out"),
            "--state-dir", str(tmp_path / "state"),
        ])
        assert result.exit_code != 0

    def test_run_with_approval_file_dispatches(self, tmp_path: Path):
        req = AuditGovernanceRequest(
            repo_id="videofoundry",
            audit_type="representative",
            requested_by="alice",
            requested_reason="test",
            urgency="urgent",
            related_suite_report_path="/suite.json",
        )
        req_path = tmp_path / "req.json"
        req_path.write_text(req.model_dump_json(indent=2), encoding="utf-8")
        dec_path, decision = _make_decision_file(tmp_path, req)

        approval = make_manual_approval(decision, req, approved_by="bob")
        approval_path = tmp_path / "approval.json"
        approval_path.write_text(approval.model_dump_json(indent=2), encoding="utf-8")

        dispatch_mock = MagicMock(return_value=_make_dispatch_result())
        with patch(_DISPATCH_TARGET, dispatch_mock):
            result = _runner.invoke(app, [
                "run",
                "--request", str(req_path),
                "--approval", str(approval_path),
                "--known-repos", "videofoundry",
                "--known-types", "representative",
                "--output-dir", str(tmp_path / "out"),
                "--state-dir", str(tmp_path / "state"),
            ])
        assert dispatch_mock.called, f"Dispatch not called. Output: {result.output}"
        assert result.exit_code == 0

    def test_run_missing_request_file_exits_nonzero(self, tmp_path: Path):
        result = _runner.invoke(app, [
            "run",
            "--request", str(tmp_path / "nonexistent.json"),
            "--output-dir", str(tmp_path / "out"),
            "--state-dir", str(tmp_path / "state"),
        ])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# cmd_inspect
# ---------------------------------------------------------------------------

class TestCmdInspect:
    def test_inspect_written_report(self, tmp_path: Path):
        from operations_center.audit_governance import run_governed_audit, load_governance_report

        req = AuditGovernanceRequest(
            repo_id="videofoundry",
            audit_type="representative",
            requested_by="alice",
            requested_reason="inspect test",
            urgency="normal",
            related_suite_report_path="/suite.json",
        )
        cfg = GovernanceConfig(
            known_repos=["videofoundry"],
            known_audit_types={"videofoundry": ["representative"]},
        )
        dispatch_mock = MagicMock(return_value=_make_dispatch_result())
        with patch(_DISPATCH_TARGET, dispatch_mock):
            run_result = run_governed_audit(req, governance_config=cfg, output_dir=tmp_path / "out")

        result = _runner.invoke(app, [
            "inspect",
            "--report", run_result.report_path,
        ])
        assert result.exit_code == 0
        assert "videofoundry" in result.output

    def test_inspect_missing_report_exits_nonzero(self, tmp_path: Path):
        result = _runner.invoke(app, [
            "inspect",
            "--report", str(tmp_path / "nonexistent.json"),
        ])
        assert result.exit_code != 0

    def test_inspect_invalid_report_exits_nonzero(self, tmp_path: Path):
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        result = _runner.invoke(app, [
            "inspect",
            "--report", str(bad),
        ])
        assert result.exit_code != 0
