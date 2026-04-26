"""Post-execution contract discovery for managed audit runs.

After the audit subprocess exits (with any exit code), this module:
  1. Locates run_status.json in the expected output directory by searching
     for the bucket directory whose name contains the run_id string.
  2. Validates run_status.json against the Phase 2 ManagedRunStatus contract.
  3. Resolves artifact_manifest_path using the Phase 3 resolver.

No directory scanning for artifacts. No manifest file indexing.
Discovery path: run_status.json → artifact_manifest_path → artifact_manifest.json
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from operations_center.audit_toolset import (
    ArtifactManifestPathMissingError,
    ArtifactManifestPathResolutionError,
    RunStatusContractError,
    RunStatusNotFoundError,
    load_run_status_entrypoint,
    resolve_artifact_manifest_path,
)
from operations_center.audit_toolset import ManagedAuditInvocationRequest

from .models import FailureKind


@dataclass
class PostExecutionDiscovery:
    """Result of post-execution contract discovery.

    Populated regardless of process exit code — a nonzero exit does not
    prevent discovery if the producer wrote contract files.
    """

    run_status_path: str | None = None
    artifact_manifest_path: str | None = None
    failure_kind: FailureKind | None = None
    failure_reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.failure_kind is None


def _find_run_status_path(
    expected_output_dir: Path,
    run_id: str,
) -> Path | None:
    """Locate run_status.json by searching for the bucket containing run_id.

    The VideoFoundry bucket directory name includes the run_id string following
    the naming convention: {channel_slug}_{YYYYMMDD}_{HHMMSS}_{run_id}.

    This is a targeted lookup by known run_id — not arbitrary artifact scanning.
    Only the run_status.json filename is inspected; no other files are read.
    """
    if not expected_output_dir.is_dir():
        return None
    for child in sorted(expected_output_dir.iterdir()):
        if not child.is_dir():
            continue
        if run_id in child.name:
            candidate = child / "run_status.json"
            if candidate.is_file():
                return candidate
    return None


def discover_post_execution(
    invocation: ManagedAuditInvocationRequest,
    run_id: str,
    *,
    working_dir_abs: Path | None = None,
) -> PostExecutionDiscovery:
    """Discover run_status.json and artifact_manifest_path after process exit.

    Always called after subprocess exit, regardless of exit code. A nonzero exit
    does not prevent contract discovery — the producer may have written a failed
    run_status.json that OpsCenter can read.

    Parameters
    ----------
    invocation:
        Phase 3 invocation request. Provides expected_output_dir (relative or
        absolute) and working_directory.
    run_id:
        The run identity string injected into the subprocess as AUDIT_RUN_ID.
        Used to locate the bucket directory (the bucket name contains run_id).
    working_dir_abs:
        Resolved absolute working directory (VF repo root). When None, resolved
        from invocation.working_directory.

    Returns
    -------
    PostExecutionDiscovery
        Populated with run_status_path, artifact_manifest_path, and/or
        failure_kind + failure_reason.
    """
    working_dir = working_dir_abs or Path(invocation.working_directory).resolve()
    expected_output = Path(invocation.expected_output_dir)

    if not expected_output.is_absolute():
        expected_output = (working_dir / expected_output).resolve()

    # Step 1: Targeted bucket search by run_id.
    run_status_path = _find_run_status_path(expected_output, run_id)
    if run_status_path is None:
        return PostExecutionDiscovery(
            failure_kind=FailureKind.RUN_STATUS_MISSING,
            failure_reason=(
                f"run_status.json not found in '{expected_output}' "
                f"for run_id='{run_id}'. "
                f"The managed audit may not have written contract files."
            ),
        )

    # Step 2: Validate against Phase 2 contract.
    try:
        run_status = load_run_status_entrypoint(run_status_path)
    except RunStatusNotFoundError as exc:
        return PostExecutionDiscovery(
            run_status_path=str(run_status_path),
            failure_kind=FailureKind.RUN_STATUS_MISSING,
            failure_reason=str(exc),
        )
    except RunStatusContractError as exc:
        return PostExecutionDiscovery(
            run_status_path=str(run_status_path),
            failure_kind=FailureKind.RUN_STATUS_INVALID,
            failure_reason=str(exc),
        )

    # Step 3: Resolve artifact_manifest_path.
    # artifact_manifest_path in run_status.json is relative to the VF repo root
    # (the working_directory), so base_dir = working_dir.
    try:
        manifest_path = resolve_artifact_manifest_path(
            run_status,
            base_dir=working_dir,
        )
    except ArtifactManifestPathMissingError as exc:
        return PostExecutionDiscovery(
            run_status_path=str(run_status_path),
            failure_kind=FailureKind.MANIFEST_PATH_MISSING,
            failure_reason=str(exc),
        )
    except ArtifactManifestPathResolutionError as exc:
        return PostExecutionDiscovery(
            run_status_path=str(run_status_path),
            failure_kind=FailureKind.MANIFEST_PATH_UNRESOLVABLE,
            failure_reason=str(exc),
        )

    return PostExecutionDiscovery(
        run_status_path=str(run_status_path),
        artifact_manifest_path=str(manifest_path),
    )
