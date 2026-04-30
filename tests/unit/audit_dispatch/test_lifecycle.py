# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for Phase 6 post-execution contract discovery."""

from __future__ import annotations

import json
from pathlib import Path


from operations_center.audit_dispatch.lifecycle import (
    PostExecutionDiscovery,
    _find_run_status_path,
    discover_post_execution,
)
from operations_center.audit_dispatch.models import FailureKind
from operations_center.audit_toolset import ManagedAuditInvocationRequest

_RUN_ID = "videofoundry_representative_20260426T120000Z_aabb1122"

_MINIMAL_INVOCATION = {
    "repo_id": "videofoundry",
    "audit_type": "representative",
    "run_id": _RUN_ID,
    "working_directory": ".",
    "command": "python -m tools.audit.run_representative_audit",
    "env": {"AUDIT_RUN_ID": _RUN_ID},
    "expected_output_dir": "output",
    "metadata": {},
}


def _make_invocation(**overrides) -> ManagedAuditInvocationRequest:
    data = {**_MINIMAL_INVOCATION, **overrides}
    return ManagedAuditInvocationRequest.model_validate(data)


def _write_run_status(bucket_dir: Path, run_id: str, *, manifest_rel_path: str | None) -> None:
    payload = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
        "repo_id": "videofoundry",
        "run_id": run_id,
        "audit_type": "representative",
        "status": "completed",
        "artifact_manifest_path": manifest_rel_path,
        "metadata": {},
    }
    (bucket_dir / "run_status.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _write_manifest(path: Path) -> None:
    payload = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "videofoundry",
        "repo_id": "videofoundry",
        "run_id": _RUN_ID,
        "audit_type": "representative",
        "manifest_status": "completed",
        "run_status": "completed",
        "created_at": "2026-04-26T12:00:00Z",
        "updated_at": "2026-04-26T12:00:00Z",
        "artifacts": [],
        "excluded_paths": [],
        "limitations": [],
        "errors": [],
        "warnings": [],
        "metadata": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# _find_run_status_path
# ---------------------------------------------------------------------------


class TestFindRunStatusPath:
    def test_finds_bucket_containing_run_id(self, tmp_path: Path) -> None:
        bucket = tmp_path / f"MyTopic_20260426_120000_{_RUN_ID}"
        bucket.mkdir()
        (bucket / "run_status.json").write_text("{}", encoding="utf-8")
        found = _find_run_status_path(tmp_path, _RUN_ID)
        assert found == bucket / "run_status.json"

    def test_returns_none_if_no_bucket_matches(self, tmp_path: Path) -> None:
        other = tmp_path / "unrelated_bucket"
        other.mkdir()
        (other / "run_status.json").write_text("{}", encoding="utf-8")
        assert _find_run_status_path(tmp_path, _RUN_ID) is None

    def test_returns_none_if_output_dir_missing(self, tmp_path: Path) -> None:
        assert _find_run_status_path(tmp_path / "nonexistent", _RUN_ID) is None

    def test_ignores_bucket_without_run_status_json(self, tmp_path: Path) -> None:
        bucket = tmp_path / f"Topic_{_RUN_ID}"
        bucket.mkdir()
        # no run_status.json written
        assert _find_run_status_path(tmp_path, _RUN_ID) is None

    def test_does_not_match_on_partial_id(self, tmp_path: Path) -> None:
        bucket = tmp_path / "Topic_aabb"  # only partial match
        bucket.mkdir()
        (bucket / "run_status.json").write_text("{}", encoding="utf-8")
        assert _find_run_status_path(tmp_path, _RUN_ID) is None


# ---------------------------------------------------------------------------
# discover_post_execution
# ---------------------------------------------------------------------------


class TestDiscoverPostExecution:
    def test_missing_output_dir_returns_run_status_missing(self, tmp_path: Path) -> None:
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="nonexistent_output",
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.failure_kind == FailureKind.RUN_STATUS_MISSING
        assert not result.succeeded

    def test_no_matching_bucket_returns_run_status_missing(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="output",
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.failure_kind == FailureKind.RUN_STATUS_MISSING

    def test_invalid_run_status_json_returns_contract_error(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        bucket = output_dir / f"Topic_{_RUN_ID}"
        bucket.mkdir()
        (bucket / "run_status.json").write_text("not valid json", encoding="utf-8")
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="output",
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.failure_kind in (
            FailureKind.RUN_STATUS_INVALID,
            FailureKind.RUN_STATUS_MISSING,
        )

    def test_run_status_without_manifest_path_returns_manifest_missing(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        bucket = output_dir / f"Topic_{_RUN_ID}"
        bucket.mkdir()
        _write_run_status(bucket, _RUN_ID, manifest_rel_path=None)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="output",
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.run_status_path is not None
        assert result.failure_kind == FailureKind.MANIFEST_PATH_MISSING

    def test_successful_discovery_returns_both_paths(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        bucket = output_dir / f"Topic_{_RUN_ID}"
        bucket.mkdir()
        manifest = bucket / "artifact_manifest.json"
        _write_manifest(manifest)
        manifest_rel = str(manifest.relative_to(tmp_path))
        _write_run_status(bucket, _RUN_ID, manifest_rel_path=manifest_rel)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="output",
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.succeeded
        assert result.run_status_path is not None
        assert result.artifact_manifest_path is not None

    def test_run_status_path_included_on_manifest_failure(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        bucket = output_dir / f"Topic_{_RUN_ID}"
        bucket.mkdir()
        _write_run_status(bucket, _RUN_ID, manifest_rel_path=None)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="output",
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.run_status_path is not None
        assert "run_status.json" in result.run_status_path

    def test_failure_reason_is_set_on_failure(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="output",
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.failure_reason is not None
        assert len(result.failure_reason) > 0

    def test_resolves_relative_expected_output_dir(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "relative_output"
        output_dir.mkdir()
        bucket = output_dir / f"Topic_{_RUN_ID}"
        bucket.mkdir()
        manifest = bucket / "artifact_manifest.json"
        _write_manifest(manifest)
        manifest_rel = str(manifest.relative_to(tmp_path))
        _write_run_status(bucket, _RUN_ID, manifest_rel_path=manifest_rel)
        inv = _make_invocation(
            working_directory=str(tmp_path),
            expected_output_dir="relative_output",  # relative, not absolute
        )
        result = discover_post_execution(inv, _RUN_ID, working_dir_abs=tmp_path)
        assert result.succeeded


class TestPostExecutionDiscoveryProperties:
    def test_succeeded_true_when_no_failure_kind(self) -> None:
        d = PostExecutionDiscovery(
            run_status_path="/a/b/run_status.json",
            artifact_manifest_path="/a/b/artifact_manifest.json",
        )
        assert d.succeeded is True

    def test_succeeded_false_when_failure_kind_set(self) -> None:
        d = PostExecutionDiscovery(failure_kind=FailureKind.RUN_STATUS_MISSING)
        assert d.succeeded is False
