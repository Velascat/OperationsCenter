"""Top-level dispatch_managed_audit() entry point.

Orchestration flow:
  1. Prepare invocation (Phase 4 identity + Phase 3 command resolution).
  2. Resolve absolute working directory from managed repo config.
  3. Acquire per-repo lock (raises RepoLockAlreadyHeldError if held).
  4. Execute subprocess, capture stdout/stderr to log files.
  5. Release lock in finally.
  6. Discover run_status.json and artifact_manifest_path (Phase 3).
  7. Return ManagedAuditDispatchResult.

Hard boundary: this module never imports VideoFoundry code.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations_center.audit_toolset import (
    ManagedAuditCommandUnavailableError,
    ManagedAuditTypeUnsupportedError,
    ManagedRepoCapabilityError,
    ManagedRepoNotFoundError,
)
from operations_center.managed_repos.loader import load_managed_repo_config
from operations_center.run_identity.errors import RunIdentityError
from operations_center.run_identity.generator import prepare_managed_audit_invocation

from .errors import AuditDispatchConfigError, RepoLockAlreadyHeldError
from .executor import ManagedAuditExecutor
from .lifecycle import discover_post_execution
from .locks import acquire_audit_lock
from .models import (
    DispatchStatus,
    FailureKind,
    ManagedAuditDispatchRequest,
    ManagedAuditDispatchResult,
)

# OperationsCenter repo root — used to resolve relative repo_root paths from config.
# api.py is at: src/operations_center/audit_dispatch/api.py
# parents[0] = audit_dispatch/, [1] = operations_center/, [2] = src/, [3] = OC root
_OC_ROOT = Path(__file__).resolve().parents[3]

_CONFIG_ERRORS = (
    FileNotFoundError,  # from load_managed_repo_config before ManagedRepoNotFoundError wrapping
    ManagedRepoNotFoundError,
    ManagedRepoCapabilityError,
    ManagedAuditTypeUnsupportedError,
    ManagedAuditCommandUnavailableError,
    RunIdentityError,
)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _resolve_abs_working_dir(
    invocation_working_dir: str,
    repo_id: str,
    config_dir: Path | str | None,
    cwd_override: str | None,
) -> str:
    """Resolve the absolute working directory for the subprocess.

    If cwd_override is provided, it is returned as-is (caller is responsible
    for providing an absolute path).

    Otherwise, invocation_working_dir (e.g., ".") is resolved against the
    managed repo's absolute repo_root path, which is itself resolved against
    the OpsCenter repository root.
    """
    if cwd_override is not None:
        return cwd_override

    wd = Path(invocation_working_dir)
    if wd.is_absolute():
        return str(wd)

    # invocation_working_dir is relative — resolve against repo_root from config.
    # repo_root in YAML (e.g., "../VideoFoundry") is relative to OpsCenter root.
    config = load_managed_repo_config(repo_id, config_dir=config_dir)
    repo_root_abs = (_OC_ROOT / config.repo_root).resolve()
    return str((repo_root_abs / wd).resolve())


def _default_log_dir(working_dir_abs: str, run_id: str) -> Path:
    """Default stdout/stderr log directory within the managed repo working directory."""
    return Path(working_dir_abs) / "tools" / "audit" / "report" / "dispatch" / run_id


def dispatch_managed_audit(
    request: ManagedAuditDispatchRequest,
    *,
    config_dir: Path | str | None = None,
    log_dir: Path | str | None = None,
) -> ManagedAuditDispatchResult:
    """Prepare, execute, and discover results of a managed audit run.

    Parameters
    ----------
    request:
        Validated dispatch request.
    config_dir:
        Override path to managed repo YAML configs directory.
        Defaults to OpsCenter's built-in config/managed_repos/.
    log_dir:
        Override directory for stdout/stderr log files.
        Defaults to {working_dir}/tools/audit/report/dispatch/{run_id}/.

    Returns
    -------
    ManagedAuditDispatchResult
        Always returned — process and contract discovery failures are returned
        as structured results, not raised exceptions.

    Raises
    ------
    RepoLockAlreadyHeldError
        If a concurrent audit is already dispatched for request.repo_id.
    AuditDispatchConfigError
        If the repo config, audit type, or command cannot be resolved.
        This indicates a programming or configuration error, not an operational
        failure — the caller must fix the configuration before retrying.
    """
    started_at = _now_utc()

    # Step 1: Prepare invocation (Phase 4 identity + Phase 3 command).
    # Config/capability errors are programming errors — raise, don't return.
    effective_extra_env = (
        dict(request.base_env) if request.base_env is not None else os.environ.copy()
    )
    try:
        prepared = prepare_managed_audit_invocation(
            request.repo_id,
            request.audit_type,
            config_dir=config_dir,
            allow_not_yet_run=request.allow_unverified_command,
            extra_env=effective_extra_env,
            metadata=dict(request.metadata),
        )
    except _CONFIG_ERRORS as exc:
        raise AuditDispatchConfigError(str(exc)) from exc

    invocation = prepared.request
    identity = prepared.identity
    run_id = identity.run_id

    # Step 2: Resolve absolute working directory.
    working_dir_abs = _resolve_abs_working_dir(
        invocation.working_directory,
        request.repo_id,
        config_dir,
        request.cwd_override,
    )
    # Patch the invocation so lifecycle.py and executor share the same resolved path.
    # ManagedAuditInvocationRequest is not frozen so direct assignment is valid.
    invocation.working_directory = working_dir_abs

    effective_log_dir = (
        Path(log_dir) if log_dir is not None else _default_log_dir(working_dir_abs, run_id)
    )
    executor = ManagedAuditExecutor(effective_log_dir)

    # Step 3: Acquire per-repo lock — raises if already held.
    lock = acquire_audit_lock(request.repo_id)
    try:
        proc_result = executor.execute(
            invocation,
            timeout_seconds=request.timeout_seconds,
        )
    finally:
        lock.release()

    # Step 4: Post-execution contract discovery.
    # Always attempted, even on nonzero exit — the producer may have written
    # a failed run_status.json that contains useful contract information.
    discovery = discover_post_execution(
        invocation,
        run_id,
        working_dir_abs=Path(working_dir_abs),
    )

    # Step 5: Determine final status and failure kind.
    if proc_result.timed_out:
        status = DispatchStatus.INTERRUPTED
        failure_kind = FailureKind.PROCESS_TIMEOUT
        error = proc_result.error or f"timed out after {request.timeout_seconds}s"
    elif proc_result.error is not None:
        # Executor-level error (e.g., Popen failed to launch).
        status = DispatchStatus.FAILED
        failure_kind = FailureKind.EXECUTOR_ERROR
        error = proc_result.error
    elif not discovery.succeeded:
        # Contract discovery failed — report discovery failure as primary.
        # Include process exit info in error string if nonzero.
        status = DispatchStatus.FAILED
        failure_kind = discovery.failure_kind or FailureKind.UNKNOWN
        exit_note = (
            f" (process exit code: {proc_result.exit_code})"
            if proc_result.exit_code not in (None, 0)
            else ""
        )
        error = f"{discovery.failure_reason}{exit_note}"
    elif proc_result.exit_code != 0:
        # Process failed but contract files are present — nonzero exit is primary failure.
        status = DispatchStatus.FAILED
        failure_kind = FailureKind.PROCESS_NONZERO_EXIT
        error = f"process exited with code {proc_result.exit_code}"
    else:
        status = DispatchStatus.COMPLETED
        failure_kind = None
        error = None

    return ManagedAuditDispatchResult(
        repo_id=request.repo_id,
        audit_type=request.audit_type,
        run_id=run_id,
        status=status,
        failure_kind=failure_kind,
        process_exit_code=proc_result.exit_code,
        started_at=started_at,
        ended_at=proc_result.ended_at,
        duration_seconds=(proc_result.ended_at - started_at).total_seconds(),
        run_status_path=discovery.run_status_path,
        artifact_manifest_path=discovery.artifact_manifest_path,
        stdout_path=str(proc_result.stdout_path),
        stderr_path=str(proc_result.stderr_path),
        error=error,
        metadata=dict(request.metadata),
    )
