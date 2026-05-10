# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the ``operations-center-run-show`` entrypoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from operations_center.entrypoints.run_show.main import app


def _trace_payload() -> dict:
    return {
        "trace_id": "trace-1",
        "record_id": "rec-1",
        "headline": "SUCCEEDED | direct_local @ aider_local | run=abcd1234",
        "status": "succeeded",
        "summary": "Run abcd1234; changed 0 files",
        "key_artifacts": [],
        "changed_files_summary": "no files changed",
        "validation_summary": {"status": "skipped"},
        "warnings": ["validation was skipped for this run"],
        "backend_detail_refs": [],
        "runtime_invocation_ref": {
            "invocation_id": "iv-1",
            "runtime_name": "direct_local",
            "runtime_kind": "subprocess",
            "stdout_path": "/tmp/run-show-fixture-stdout.txt",
            "stderr_path": "/tmp/run-show-fixture-stderr.txt",
            "artifact_directory": "/tmp/run-show-fixture",
        },
        "routing": {
            "decision_id": "dec-1",
            "selected_lane": "aider_local",
            "selected_backend": "direct_local",
            "policy_rule_matched": "lint_fix_to_aider_local",
            "rationale": "lint_fix tasks default to aider_local",
            "switchboard_version": "0.4.2",
            "confidence": 0.87,
            "alternatives_considered": ["claude_cli"],
        },
    }


def _seed_run(root: Path, run_id: str) -> Path:
    rd = root / run_id
    rd.mkdir(parents=True, exist_ok=True)
    trace = rd / "execution_trace.json"
    trace.write_text(json.dumps(_trace_payload()), encoding="utf-8")
    return trace


def test_show_by_run_id_with_explicit_root(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _seed_run(runs_root, "abcd1234-aaaa-bbbb-cccc-deadbeef0001")

    result = CliRunner().invoke(
        app,
        ["abcd1234-aaaa-bbbb-cccc-deadbeef0001", "--root", str(runs_root)],
    )
    assert result.exit_code == 0, result.output
    assert "SUCCEEDED" in result.output
    assert "iv-1" in result.output
    assert "lint_fix_to_aider_local" in result.output
    assert "0.4.2" in result.output


def test_show_resolves_unambiguous_prefix(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _seed_run(runs_root, "abcd1234-aaaa-bbbb-cccc-deadbeef0001")

    result = CliRunner().invoke(app, ["abcd1234", "--root", str(runs_root)])
    assert result.exit_code == 0, result.output
    assert "SUCCEEDED" in result.output


def test_show_rejects_ambiguous_prefix(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _seed_run(runs_root, "abcd1234-aaaa-bbbb-cccc-deadbeef0001")
    _seed_run(runs_root, "abcd1234-bbbb-bbbb-cccc-deadbeef0002")

    result = CliRunner().invoke(app, ["abcd1234", "--root", str(runs_root)])
    assert result.exit_code != 0
    assert "ambiguous" in (result.output + (result.stderr or "")).lower() or \
           "ambiguous" in (result.output + str(result.exception or "")).lower()


def test_show_with_explicit_trace_path(tmp_path: Path) -> None:
    trace_path = _seed_run(tmp_path / "runs", "abcd1234-aaaa-bbbb-cccc-deadbeef0001")
    result = CliRunner().invoke(app, ["--trace", str(trace_path)])
    assert result.exit_code == 0, result.output
    assert "iv-1" in result.output


def test_show_json_emits_full_payload(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    _seed_run(runs_root, "abcd1234-aaaa-bbbb-cccc-deadbeef0001")

    result = CliRunner().invoke(
        app,
        ["abcd1234", "--root", str(runs_root), "--json"],
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["runtime_invocation_ref"]["invocation_id"] == "iv-1"
    assert parsed["routing"]["switchboard_version"] == "0.4.2"


def test_show_complains_when_run_id_missing(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    result = CliRunner().invoke(app, ["nonexistent", "--root", str(runs_root)])
    assert result.exit_code != 0


def test_show_handles_trace_without_ref_or_routing(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    rd = runs_root / "demo-run-1"
    rd.mkdir(parents=True)
    (rd / "execution_trace.json").write_text(
        json.dumps({
            "trace_id": "t", "record_id": "r",
            "headline": "SUCCEEDED | demo_stub @ x | run=demo",
            "status": "succeeded", "summary": "ok",
            "key_artifacts": [], "changed_files_summary": "",
            "validation_summary": {"status": "skipped"},
            "warnings": [], "backend_detail_refs": [],
        }),
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["demo-run-1", "--root", str(runs_root)])
    assert result.exit_code == 0, result.output
    assert "no routing block" in result.output
    assert "no runtime_invocation_ref" in result.output
