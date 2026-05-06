# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ER-002 — Run Memory primitive tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typer.testing import CliRunner

from operations_center.contracts.enums import ExecutionStatus
from operations_center.contracts.execution import ExecutionResult
from operations_center.run_memory import (
    RunMemoryIndexWriter,
    RunMemoryQuery,
    RunMemoryQueryService,
    RunMemoryRecord,
    SourceType,
    deterministic_record_id,
    rebuild_index_from_artifacts,
    record_execution_result,
)
from operations_center.run_memory.cli import app


def _make_result(*, run_id: str = "r1", status=ExecutionStatus.SUCCEEDED, success: bool = True) -> ExecutionResult:
    return ExecutionResult(
        run_id=run_id,
        proposal_id=f"p-{run_id}",
        decision_id=f"d-{run_id}",
        status=status,
        success=success,
        failure_reason=None if success else "boom",
    )


# ---------------------------------------------------------------------------
# Deterministic IDs
# ---------------------------------------------------------------------------


class TestDeterministicId:
    def test_same_input_same_id(self) -> None:
        assert deterministic_record_id("abc") == deterministic_record_id("abc")

    def test_different_inputs_different_ids(self) -> None:
        assert deterministic_record_id("abc") != deterministic_record_id("abcd")

    def test_format_prefix(self) -> None:
        assert deterministic_record_id("xyz").startswith("rmr-")


# ---------------------------------------------------------------------------
# Single write site (record_execution_result)
# ---------------------------------------------------------------------------


class TestRecordExecutionResult:
    def test_writes_jsonl_line(self, tmp_path: Path) -> None:
        result = _make_result(run_id="r-success")
        rec = record_execution_result(
            result, tmp_path, repo_id="velascat/foo", tags=("nightly",)
        )
        path = tmp_path / "records.jsonl"
        assert path.exists()
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["record_id"] == rec.record_id
        assert data["repo_id"] == "velascat/foo"
        assert data["status"] == "succeeded"
        assert data["source_type"] == "execution_result"
        assert "nightly" in data["tags"]

    def test_failure_result_indexed(self, tmp_path: Path) -> None:
        result = _make_result(run_id="r-fail", status=ExecutionStatus.FAILED, success=False)
        record_execution_result(result, tmp_path)
        lines = (tmp_path / "records.jsonl").read_text().splitlines()
        data = json.loads(lines[0])
        assert data["status"] == "failed"
        assert "boom" in data["summary"]

    def test_two_results_two_distinct_record_ids(self, tmp_path: Path) -> None:
        record_execution_result(_make_result(run_id="ra"), tmp_path)
        record_execution_result(_make_result(run_id="rb"), tmp_path)
        lines = (tmp_path / "records.jsonl").read_text().splitlines()
        ids = {json.loads(ln)["record_id"] for ln in lines}
        assert len(ids) == 2

    def test_record_id_is_deterministic_across_calls(self, tmp_path: Path) -> None:
        r1 = record_execution_result(_make_result(run_id="same"), tmp_path)
        r2 = record_execution_result(_make_result(run_id="same"), tmp_path)
        assert r1.record_id == r2.record_id


# ---------------------------------------------------------------------------
# Query service
# ---------------------------------------------------------------------------


def _seed(tmp_path: Path) -> None:
    record_execution_result(
        _make_result(run_id="r-success"),
        tmp_path,
        repo_id="velascat/api",
        tags=("nightly", "smoke"),
        contract_kinds=("execution_request",),
        summary="user serializer fix landed",
    )
    record_execution_result(
        _make_result(run_id="r-fail", status=ExecutionStatus.FAILED, success=False),
        tmp_path,
        repo_id="velascat/web",
        tags=("nightly",),
        contract_kinds=("execution_result",),
        summary="login flow regression",
    )


class TestQueryService:
    def test_repo_filter(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(RunMemoryQuery(repo_id="velascat/api"))
        assert {r.run_id for r in results} == {"r-success"}

    def test_status_filter(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(RunMemoryQuery(status="failed"))
        assert {r.run_id for r in results} == {"r-fail"}

    def test_tag_filter(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(RunMemoryQuery(tag="smoke"))
        assert {r.run_id for r in results} == {"r-success"}

    def test_contract_kind_filter(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(RunMemoryQuery(contract_kind="execution_result"))
        assert {r.run_id for r in results} == {"r-fail"}

    def test_text_substring_in_summary(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(RunMemoryQuery(text="serializer"))
        assert {r.run_id for r in results} == {"r-success"}

    def test_text_substring_case_insensitive(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(RunMemoryQuery(text="LOGIN"))
        assert {r.run_id for r in results} == {"r-fail"}

    def test_text_no_match_returns_empty(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        assert svc.query(RunMemoryQuery(text="zzz-nope-zzz")) == []

    def test_combined_filters_are_and(self, tmp_path: Path) -> None:
        _seed(tmp_path)
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(RunMemoryQuery(tag="nightly", status="failed"))
        assert {r.run_id for r in results} == {"r-fail"}

    def test_time_range_filter(self, tmp_path: Path) -> None:
        # Hand-craft records with controlled timestamps.
        writer = RunMemoryIndexWriter(tmp_path)
        now = datetime.now(tz=timezone.utc)
        old = now - timedelta(days=7)
        writer.append(
            RunMemoryRecord(
                record_id="rmr-old",
                run_id="r-old",
                request_id="p",
                result_id="x",
                repo_id="r",
                artifact_paths=(),
                contract_kinds=(),
                status="succeeded",
                summary="old",
                tags=(),
                created_at=old.isoformat().replace("+00:00", "Z"),
                source_type=SourceType.EXECUTION_RESULT,
            )
        )
        writer.append(
            RunMemoryRecord(
                record_id="rmr-new",
                run_id="r-new",
                request_id="p",
                result_id="y",
                repo_id="r",
                artifact_paths=(),
                contract_kinds=(),
                status="succeeded",
                summary="new",
                tags=(),
                created_at=now.isoformat().replace("+00:00", "Z"),
                source_type=SourceType.EXECUTION_RESULT,
            )
        )
        svc = RunMemoryQueryService(tmp_path)
        results = svc.query(
            RunMemoryQuery(time_range=(now - timedelta(days=1), now + timedelta(days=1)))
        )
        assert {r.run_id for r in results} == {"r-new"}

    def test_empty_index_returns_empty(self, tmp_path: Path) -> None:
        svc = RunMemoryQueryService(tmp_path)
        assert svc.query(RunMemoryQuery()) == []


# ---------------------------------------------------------------------------
# Rebuild from on-disk artifacts
# ---------------------------------------------------------------------------


class TestRebuild:
    def _write_artifact(self, dirp: Path, run_id: str, status: str = "succeeded") -> Path:
        path = dirp / f"execution_result_{run_id}.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "proposal_id": f"p-{run_id}",
                    "decision_id": f"d-{run_id}",
                    "status": status,
                    "success": status == "succeeded",
                    "repo_id": "velascat/api",
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_rebuild_from_artifacts(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        index = tmp_path / "index"
        self._write_artifact(artifacts, "r1")
        self._write_artifact(artifacts, "r2", status="failed")
        n = rebuild_index_from_artifacts(artifacts, index)
        assert n == 2
        svc = RunMemoryQueryService(index)
        runs = {r.run_id for r in svc.all()}
        assert runs == {"r1", "r2"}

    def test_rebuild_is_idempotent(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        index = tmp_path / "index"
        self._write_artifact(artifacts, "r1")
        self._write_artifact(artifacts, "r2")
        rebuild_index_from_artifacts(artifacts, index)
        first = RunMemoryQueryService(index).all()
        rebuild_index_from_artifacts(artifacts, index)
        second = RunMemoryQueryService(index).all()
        assert [r.record_id for r in first] == [r.record_id for r in second]
        # File should contain exactly len(first) lines (no duplication).
        lines = (index / "records.jsonl").read_text().splitlines()
        assert len([ln for ln in lines if ln.strip()]) == len(first)

    def test_rebuild_skips_non_matching_files(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "unrelated.json").write_text('{"foo": 1}', encoding="utf-8")
        (artifacts / "execution_request_x.json").write_text('{"foo": 1}', encoding="utf-8")  # wrong prefix
        self._write_artifact(artifacts, "r1")
        index = tmp_path / "index"
        n = rebuild_index_from_artifacts(artifacts, index)
        assert n == 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_query_json_empty(self, tmp_path: Path) -> None:
        result = self.runner.invoke(
            app, ["query", "--index-dir", str(tmp_path), "--json"]
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == []

    def test_query_filters(self, tmp_path: Path) -> None:
        record_execution_result(
            _make_result(run_id="r-success"),
            tmp_path,
            repo_id="velascat/api",
            tags=("nightly",),
            summary="serializer fix",
        )
        result = self.runner.invoke(
            app,
            ["query", "--index-dir", str(tmp_path), "--text", "serializer", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["run_id"] == "r-success"

    def test_rebuild(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "a"
        artifacts.mkdir()
        (artifacts / "execution_result_r1.json").write_text(
            json.dumps(
                {
                    "run_id": "r1",
                    "proposal_id": "p",
                    "decision_id": "d",
                    "status": "succeeded",
                    "success": True,
                }
            ),
            encoding="utf-8",
        )
        result = self.runner.invoke(
            app,
            [
                "rebuild",
                "--artifacts-dir",
                str(artifacts),
                "--index-dir",
                str(tmp_path / "i"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "rebuilt" in result.output
