# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for the post-dispatch coverage analysis bridge."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


from operations_center.audit_governance.coverage_analysis import (
    _find_coverage_json,
    _summarize,
    run_post_dispatch_coverage_audit,
)


def _write_manifest_with_coverage(tmp_path: Path) -> Path:
    """Create a minimal valid manifest pointing at a coverage.json artifact."""
    bucket = tmp_path / "bucket"
    bucket.mkdir()
    coverage_json = bucket / "coverage.json"
    coverage_json.write_text(json.dumps({"meta": {"version": "7.6.1"}, "files": {}}))
    manifest_path = bucket / "artifact_manifest.json"
    payload = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "example_managed_repo",
        "repo_id": "example_managed_repo",
        "run_id": "test_run",
        "audit_type": "audit_type_1",
        "manifest_status": "completed",
        "run_status": "completed",
        "created_at": "2026-05-04T00:00:00Z",
        "updated_at": "2026-05-04T00:01:00Z",
        "finalized_at": "2026-05-04T00:01:00Z",
        "artifact_root": None,
        "run_root": "bucket",
        "artifacts": [
            {
                "artifact_id": "example_managed_repo:audit_type_1:lifecycle:coverage",
                "artifact_kind": "json_report",
                "path": str(coverage_json.relative_to(tmp_path)),
                "relative_path": "coverage.json",
                "location": "run_root",
                "path_role": "primary",
                "source_stage": "lifecycle",
                "status": "present",
                "created_at": None,
                "updated_at": "2026-05-04T00:01:00Z",
                "size_bytes": 64,
                "content_type": "application/json",
                "checksum": None,
                "consumer_types": ["automated_analysis"],
                "valid_for": ["current_run_only"],
                "limitations": [],
                "description": "",
                "metadata": {},
            },
        ],
        "excluded_paths": [],
        "warnings": [],
        "errors": [],
        "limitations": [],
        "metadata": {},
    }
    manifest_path.write_text(json.dumps(payload))
    return manifest_path


# ---------------------------------------------------------------------------
# _find_coverage_json
# ---------------------------------------------------------------------------


class TestFindCoverageJson:
    def test_finds_coverage_json_in_manifest(self, tmp_path: Path):
        mp = _write_manifest_with_coverage(tmp_path)
        path = _find_coverage_json(mp)
        assert path is not None
        assert path.name == "coverage.json"

    def test_returns_none_when_manifest_missing(self, tmp_path: Path):
        assert _find_coverage_json(tmp_path / "nope.json") is None

    def test_returns_none_when_manifest_corrupt(self, tmp_path: Path):
        mp = tmp_path / "manifest.json"
        mp.write_text("{not json")
        assert _find_coverage_json(mp) is None


# ---------------------------------------------------------------------------
# _summarize
# ---------------------------------------------------------------------------


class TestSummarize:
    def test_parses_custodian_findings(self, tmp_path: Path):
        cov = tmp_path / "coverage.json"
        cov.write_text("{}")
        stdout = json.dumps({
            "patterns": {
                "COVERAGE": {
                    "count": 4,
                    "samples": [
                        "src/foo/bar.py:0: CV1_MODULE_UNEXECUTED 0/12 statements",
                        "src/foo/baz.py:42: CV2_FUNCTION_UNEXECUTED 'func_x' never executed",
                        "src/foo/qux.py:0: CV2_FUNCTION_UNEXECUTED 'func_y' never executed",
                        "src/foo/zoo.py:0: CV3_MODULE_BELOW_MIN_COVERAGE 30%",
                    ],
                },
            },
        })
        summary = _summarize(stdout, exit_code=0, coverage_json_path=cov)
        assert summary.findings_total == 4
        assert summary.cv1_count == 1
        assert summary.cv2_count == 2
        assert summary.cv3_count == 1
        assert len(summary.sample_findings) == 4

    def test_handles_invalid_json(self, tmp_path: Path):
        summary = _summarize("not json", exit_code=0, coverage_json_path=None)
        assert summary.error is not None
        assert "not valid JSON" in summary.error

    def test_handles_missing_coverage_block(self, tmp_path: Path):
        stdout = json.dumps({"patterns": {}})
        summary = _summarize(stdout, exit_code=0, coverage_json_path=None)
        assert summary.findings_total == 0
        assert summary.error is None


# ---------------------------------------------------------------------------
# run_post_dispatch_coverage_audit
# ---------------------------------------------------------------------------


class TestRunPostDispatchCoverageAudit:
    def test_no_coverage_json_returns_error_summary(self, tmp_path: Path):
        # Manifest exists but has no coverage.json artifact.
        bucket = tmp_path / "bucket"
        bucket.mkdir()
        mp = bucket / "artifact_manifest.json"
        mp.write_text(json.dumps({
            "schema_version": "1.0",
            "contract_name": "managed-repo-audit",
            "producer": "example_managed_repo",
            "repo_id": "example_managed_repo",
            "run_id": "r1",
            "audit_type": "audit_type_1",
            "manifest_status": "completed",
            "run_status": "completed",
            "created_at": "2026-05-04T00:00:00Z",
            "updated_at": "2026-05-04T00:00:00Z",
            "finalized_at": "2026-05-04T00:00:00Z",
            "artifact_root": None,
            "run_root": "bucket",
            "artifacts": [],
            "excluded_paths": [],
            "warnings": [],
            "errors": [],
            "limitations": [],
            "metadata": {},
        }))
        summary = run_post_dispatch_coverage_audit(
            artifact_manifest_path=mp,
            consuming_repo_root=tmp_path,
        )
        assert summary.findings_total == 0
        assert summary.error is not None
        assert "coverage.json not found" in summary.error

    def test_invokes_custodian_subprocess(self, tmp_path: Path):
        mp = _write_manifest_with_coverage(tmp_path)
        fake_stdout = json.dumps({
            "patterns": {
                "COVERAGE": {
                    "count": 1,
                    "samples": ["src/foo.py:0: CV1_MODULE_UNEXECUTED 0/5 statements"],
                },
            },
        })

        class FakeProc:
            stdout = fake_stdout
            returncode = 0

        with patch("operations_center.audit_governance.coverage_analysis.shutil.which", return_value="/usr/bin/custodian"):
            with patch("operations_center.audit_governance.coverage_analysis.subprocess.run", return_value=FakeProc()) as mock_run:
                summary = run_post_dispatch_coverage_audit(
                    artifact_manifest_path=mp,
                    consuming_repo_root=tmp_path,
                )

        assert summary.findings_total == 1
        assert summary.cv1_count == 1
        assert summary.custodian_exit_code == 0
        # Verify the subprocess was invoked with the right flags.
        argv = mock_run.call_args[0][0]
        assert "--enable-coverage" in argv
        assert "--coverage-json" in argv
        assert "--json" in argv

    def test_custodian_unavailable_returns_error(self, tmp_path: Path):
        mp = _write_manifest_with_coverage(tmp_path)
        with patch("operations_center.audit_governance.coverage_analysis.shutil.which", return_value=None):
            summary = run_post_dispatch_coverage_audit(
                artifact_manifest_path=mp,
                consuming_repo_root=tmp_path,
            )
        assert "custodian executable not found" in summary.error

    def test_subprocess_timeout_returns_error(self, tmp_path: Path):
        import subprocess as _subprocess
        mp = _write_manifest_with_coverage(tmp_path)
        with patch("operations_center.audit_governance.coverage_analysis.shutil.which", return_value="/usr/bin/custodian"):
            with patch(
                "operations_center.audit_governance.coverage_analysis.subprocess.run",
                side_effect=_subprocess.TimeoutExpired(cmd="custodian", timeout=120),
            ):
                summary = run_post_dispatch_coverage_audit(
                    artifact_manifest_path=mp,
                    consuming_repo_root=tmp_path,
                    timeout_seconds=120,
                )
        assert "timed out" in summary.error
