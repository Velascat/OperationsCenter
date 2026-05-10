# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for Phase 6 dispatch_managed_audit() API.

Includes:
  - Unit tests (monkeypatched subprocess / invocation)
  - Integration test: fake command writes compliant contract files
  - AST boundary test: no ExampleManagedRepo imports in dispatch package
"""

from __future__ import annotations

import ast
import json
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from operations_center.audit_dispatch import (
    AuditDispatchConfigError,
    DispatchStatus,
    FailureKind,
    ManagedAuditDispatchRequest,
    RepoLockAlreadyHeldError,
    dispatch_managed_audit,
)
from operations_center.audit_dispatch.locks import ManagedRepoAuditLockRegistry
from operations_center.run_identity.generator import PreparedManagedAuditInvocation

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config" / "managed_repos"
_RUN_ID = "example_managed_repo_audit_type_1_20260426T120000Z_aabb1122"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(**overrides) -> ManagedAuditDispatchRequest:
    base = {
        "repo_id": "example_managed_repo",
        "audit_type": "audit_type_1",
        "allow_unverified_command": True,  # allow not_yet_run for tests
    }
    base.update(overrides)
    return ManagedAuditDispatchRequest(**base)


def _make_fake_invocation(tmp_path: Path, run_id: str = _RUN_ID):
    """Build a PreparedManagedAuditInvocation pointing at tmp_path."""
    from operations_center.audit_toolset.contracts import ManagedAuditInvocationRequest
    from operations_center.run_identity.models import ManagedRunIdentity

    identity = ManagedRunIdentity(
        repo_id="example_managed_repo",
        audit_type="audit_type_1",
        run_id=run_id,
        created_at=datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    )
    invocation = ManagedAuditInvocationRequest(
        repo_id="example_managed_repo",
        audit_type="audit_type_1",
        run_id=run_id,
        working_directory=str(tmp_path),
        command=f"{sys.executable} -c 'import sys; sys.exit(0)'",
        env={"AUDIT_RUN_ID": run_id},
        expected_output_dir="output",
        metadata={},
    )
    return PreparedManagedAuditInvocation(identity=identity, request=invocation)


def _write_compliant_bucket(
    base_dir: Path,
    run_id: str,
    *,
    exit_code: int = 0,
    include_manifest: bool = True,
) -> Path:
    """Create a compliant audit bucket and return the bucket directory."""
    bucket = base_dir / f"MyTopic_20260426_120000_{run_id}"
    bucket.mkdir(parents=True, exist_ok=True)

    manifest_path = bucket / "artifact_manifest.json"
    if include_manifest:
        manifest_payload = {
            "schema_version": "1.0",
            "contract_name": "managed-repo-audit",
            "producer": "example_managed_repo",
            "repo_id": "example_managed_repo",
            "run_id": run_id,
            "audit_type": "audit_type_1",
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
        manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
        manifest_rel = str(manifest_path.relative_to(base_dir.parent))
    else:
        manifest_rel = None

    run_status = {
        "schema_version": "1.0",
        "contract_name": "managed-repo-audit",
        "producer": "example_managed_repo",
        "repo_id": "example_managed_repo",
        "run_id": run_id,
        "audit_type": "audit_type_1",
        "status": "completed" if exit_code == 0 else "failed",
        "artifact_manifest_path": manifest_rel,
        "metadata": {},
    }
    (bucket / "run_status.json").write_text(json.dumps(run_status), encoding="utf-8")
    return bucket


# ---------------------------------------------------------------------------
# Config error handling
# ---------------------------------------------------------------------------


class TestDispatchConfigErrors:
    def test_unknown_repo_raises_config_error(self, tmp_path: Path) -> None:
        req = ManagedAuditDispatchRequest(
            repo_id="no_such_repo",
            audit_type="audit_type_1",
        )
        with pytest.raises(AuditDispatchConfigError, match="no_such_repo"):
            dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path)



# ---------------------------------------------------------------------------
# Lock behavior
# ---------------------------------------------------------------------------


class TestDispatchLocking:
    def test_lock_released_after_success(self, tmp_path: Path) -> None:

        # Use a fresh registry for isolation
        registry = ManagedRepoAuditLockRegistry()

        prepared = _make_fake_invocation(tmp_path)

        with patch(
            "operations_center.audit_dispatch.api.prepare_managed_audit_invocation",
            return_value=prepared,
        ), patch(
            "operations_center.audit_dispatch.api._resolve_abs_working_dir",
            return_value=str(tmp_path),
        ), patch(
            "operations_center.audit_dispatch.api.acquire_audit_lock",
            side_effect=registry.acquire,
        ):
            req = _make_request(cwd_override=str(tmp_path))
            dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path)

        assert not registry.is_held("example_managed_repo")

    def test_lock_held_error_raised_before_execution(self, tmp_path: Path) -> None:
        registry = ManagedRepoAuditLockRegistry()
        held_lock = registry.acquire("example_managed_repo")

        prepared = _make_fake_invocation(tmp_path)

        with patch(
            "operations_center.audit_dispatch.api.prepare_managed_audit_invocation",
            return_value=prepared,
        ), patch(
            "operations_center.audit_dispatch.api._resolve_abs_working_dir",
            return_value=str(tmp_path),
        ), patch(
            "operations_center.audit_dispatch.api.acquire_audit_lock",
            side_effect=registry.acquire,
        ):
            req = _make_request()
            with pytest.raises(RepoLockAlreadyHeldError):
                dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path)

        held_lock.release()


# ---------------------------------------------------------------------------
# Process and discovery behavior
# ---------------------------------------------------------------------------


class TestDispatchProcessBehavior:
    def _patched_dispatch(self, tmp_path: Path, *, command: str, run_id: str = _RUN_ID):
        """Dispatch with a fake invocation pointing at tmp_path."""
        output_dir = tmp_path / "output"
        output_dir.mkdir(exist_ok=True)

        prepared = _make_fake_invocation(tmp_path, run_id)
        prepared.request.command = command
        prepared.request.expected_output_dir = "output"

        with patch(
            "operations_center.audit_dispatch.api.prepare_managed_audit_invocation",
            return_value=prepared,
        ), patch(
            "operations_center.audit_dispatch.api._resolve_abs_working_dir",
            return_value=str(tmp_path),
        ):
            req = _make_request(cwd_override=str(tmp_path))
            return dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path / "logs")

    def test_exit_code_recorded_in_result(self, tmp_path: Path) -> None:
        result = self._patched_dispatch(
            tmp_path,
            command=f"{sys.executable} -c 'import sys; sys.exit(0)'",
        )
        assert result.process_exit_code == 0

    def test_nonzero_exit_returns_failed_result(self, tmp_path: Path) -> None:
        result = self._patched_dispatch(
            tmp_path,
            command=f"{sys.executable} -c 'import sys; sys.exit(1)'",
        )
        assert result.status == DispatchStatus.FAILED

    def test_nonzero_exit_still_attempts_discovery(self, tmp_path: Path) -> None:
        """A failing process that writes run_status.json should still have paths resolved."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        _bucket = _write_compliant_bucket(output_dir, _RUN_ID)

        # Command exits 1 but the bucket was pre-written
        prepared = _make_fake_invocation(tmp_path)
        prepared.request.command = f"{sys.executable} -c 'import sys; sys.exit(1)'"
        prepared.request.expected_output_dir = "output"

        with patch(
            "operations_center.audit_dispatch.api.prepare_managed_audit_invocation",
            return_value=prepared,
        ), patch(
            "operations_center.audit_dispatch.api._resolve_abs_working_dir",
            return_value=str(tmp_path),
        ):
            req = _make_request(cwd_override=str(tmp_path))
            result = dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path / "logs")

        assert result.run_status_path is not None
        assert result.failure_kind == FailureKind.PROCESS_NONZERO_EXIT

    def test_missing_run_status_is_explicit_failure(self, tmp_path: Path) -> None:
        result = self._patched_dispatch(
            tmp_path,
            command=f"{sys.executable} -c 'pass'",  # exits 0, no contract files written
        )
        assert result.status == DispatchStatus.FAILED
        assert result.failure_kind == FailureKind.RUN_STATUS_MISSING

    def test_stdout_path_in_result(self, tmp_path: Path) -> None:
        result = self._patched_dispatch(
            tmp_path,
            command=f"{sys.executable} -c 'pass'",
        )
        assert result.stdout_path is not None

    def test_stderr_path_in_result(self, tmp_path: Path) -> None:
        result = self._patched_dispatch(
            tmp_path,
            command=f"{sys.executable} -c 'pass'",
        )
        assert result.stderr_path is not None

    def test_run_id_in_result(self, tmp_path: Path) -> None:
        result = self._patched_dispatch(
            tmp_path,
            command=f"{sys.executable} -c 'pass'",
        )
        assert result.run_id == _RUN_ID

    def test_duration_seconds_in_result(self, tmp_path: Path) -> None:
        result = self._patched_dispatch(
            tmp_path,
            command=f"{sys.executable} -c 'pass'",
        )
        assert result.duration_seconds >= 0


# ---------------------------------------------------------------------------
# Integration test: fake command writes compliant contract files
# ---------------------------------------------------------------------------


class TestDispatchIntegration:
    """Integration test using a real fake command that writes contract files."""

    def _build_fake_command(self, tmp_path: Path, run_id: str) -> str:
        """Write a script file that creates compliant contract files, return the command."""
        output_dir = tmp_path / "output"
        bucket = output_dir / f"MyTopic_20260426_120000_{run_id}"
        manifest_path = bucket / "artifact_manifest.json"
        manifest_rel = str(manifest_path.relative_to(tmp_path))

        script_content = textwrap.dedent(f"""\
import json, pathlib
bucket = pathlib.Path({str(bucket)!r})
bucket.mkdir(parents=True, exist_ok=True)
manifest = bucket / "artifact_manifest.json"
manifest.write_text(json.dumps({{
    "schema_version": "1.0",
    "contract_name": "managed-repo-audit",
    "producer": "example_managed_repo",
    "repo_id": "example_managed_repo",
    "run_id": {run_id!r},
    "audit_type": "audit_type_1",
    "manifest_status": "completed",
    "run_status": "completed",
    "created_at": "2026-04-26T12:00:00Z",
    "updated_at": "2026-04-26T12:00:00Z",
    "artifacts": [],
    "excluded_paths": [],
    "limitations": [],
    "errors": [],
    "warnings": [],
    "metadata": {{}},
}}), encoding="utf-8")
(bucket / "run_status.json").write_text(json.dumps({{
    "schema_version": "1.0",
    "contract_name": "managed-repo-audit",
    "producer": "example_managed_repo",
    "repo_id": "example_managed_repo",
    "run_id": {run_id!r},
    "audit_type": "audit_type_1",
    "status": "completed",
    "artifact_manifest_path": {manifest_rel!r},
    "metadata": {{}},
}}), encoding="utf-8")
""")
        script_file = tmp_path / "_fake_audit.py"
        script_file.write_text(script_content, encoding="utf-8")
        return f"{sys.executable} {script_file}"

    def test_successful_dispatch_resolves_manifest_path(self, tmp_path: Path) -> None:
        run_id = _RUN_ID
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        prepared = _make_fake_invocation(tmp_path, run_id)
        prepared.request.command = self._build_fake_command(tmp_path, run_id)
        prepared.request.expected_output_dir = "output"

        with patch(
            "operations_center.audit_dispatch.api.prepare_managed_audit_invocation",
            return_value=prepared,
        ), patch(
            "operations_center.audit_dispatch.api._resolve_abs_working_dir",
            return_value=str(tmp_path),
        ):
            req = _make_request(cwd_override=str(tmp_path))
            result = dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path / "logs")

        assert result.status == DispatchStatus.COMPLETED
        assert result.run_status_path is not None
        assert result.artifact_manifest_path is not None
        assert result.process_exit_code == 0

    def test_audit_run_id_passed_in_env(self, tmp_path: Path) -> None:
        """Verify AUDIT_RUN_ID appears in the process environment."""
        run_id = _RUN_ID
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Command writes AUDIT_RUN_ID value to a file
        env_dump_path = tmp_path / "env_audit_run_id.txt"
        script = f"import os; open({str(env_dump_path)!r}, 'w').write(os.environ.get('AUDIT_RUN_ID', 'NOT_FOUND'))"
        command = f"{sys.executable} -c \"{script}\""

        prepared = _make_fake_invocation(tmp_path, run_id)
        prepared.request.command = command
        prepared.request.expected_output_dir = "output"

        with patch(
            "operations_center.audit_dispatch.api.prepare_managed_audit_invocation",
            return_value=prepared,
        ), patch(
            "operations_center.audit_dispatch.api._resolve_abs_working_dir",
            return_value=str(tmp_path),
        ):
            req = _make_request(base_env={"AUDIT_RUN_ID": run_id}, cwd_override=str(tmp_path))
            dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path / "logs")

        assert env_dump_path.read_text(encoding="utf-8") == run_id

    def test_dispatch_does_not_scan_directories_for_artifacts(self, tmp_path: Path) -> None:
        """Dispatch never returns artifact contents — only paths to contract files."""
        run_id = _RUN_ID
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        prepared = _make_fake_invocation(tmp_path, run_id)
        prepared.request.command = f"{sys.executable} -c 'pass'"
        prepared.request.expected_output_dir = "output"

        with patch(
            "operations_center.audit_dispatch.api.prepare_managed_audit_invocation",
            return_value=prepared,
        ), patch(
            "operations_center.audit_dispatch.api._resolve_abs_working_dir",
            return_value=str(tmp_path),
        ):
            req = _make_request(cwd_override=str(tmp_path))
            result = dispatch_managed_audit(req, config_dir=_CONFIG_DIR, log_dir=tmp_path / "logs")

        # Result contains paths, not artifact content
        assert not hasattr(result, "artifacts")
        assert not hasattr(result, "artifact_entries")


# ---------------------------------------------------------------------------
# AST boundary check — no ExampleManagedRepo imports
# ---------------------------------------------------------------------------


class TestNoBoundaryViolation:
    def test_no_example_managed_repo_imports_in_dispatch_package(self) -> None:
        """Verify the audit_dispatch package never imports ExampleManagedRepo code."""
        pkg_root = Path(__file__).parents[3] / "src" / "operations_center" / "audit_dispatch"
        assert pkg_root.is_dir(), f"audit_dispatch package not found at {pkg_root}"

        violations: list[str] = []
        for py_file in sorted(pkg_root.glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "example_managed_repo" in alias.name.lower() or alias.name.startswith("tools.audit"):
                            violations.append(f"{py_file.name}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if "example_managed_repo" in mod.lower() or mod.startswith("tools.audit"):
                        violations.append(f"{py_file.name}: from {mod} import ...")

        assert not violations, (
            "audit_dispatch imports ExampleManagedRepo code — hard boundary violated:\n"
            + "\n".join(f"  {v}" for v in violations)
        )
