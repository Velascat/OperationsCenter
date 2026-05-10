# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Run status and manifest discovery for managed audit outputs.

The only permitted discovery chain is:

    run_status.json
        → artifact_manifest_path (field)
            → artifact_manifest.json

No directory scanning.
No path inference.
No fallback crawling.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from operations_center.audit_contracts.run_status import ManagedRunStatus

from .errors import (
    ArtifactManifestPathMissingError,
    ArtifactManifestPathResolutionError,
    RunStatusContractError,
    RunStatusNotFoundError,
)


def load_run_status_entrypoint(path: Path | str) -> ManagedRunStatus:
    """Read and validate run_status.json against the Phase 2 contract.

    Parameters
    ----------
    path:
        Absolute or relative path to the run_status.json file.

    Returns
    -------
    ManagedRunStatus
        Validated contract model.  Note: is_compliant may be False for
        legacy or pre-Phase-5 files — callers should check explicitly.

    Raises
    ------
    RunStatusNotFoundError
        The file does not exist.
    RunStatusContractError
        The file exists but fails Phase 2 Pydantic validation.
    """
    p = Path(path)
    if not p.exists():
        raise RunStatusNotFoundError(
            f"run_status.json not found: {p}"
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        raw.pop("_example_note", None)
    except (OSError, json.JSONDecodeError) as exc:
        raise RunStatusContractError(
            f"Failed to read or parse run_status.json at {p}: {exc}"
        ) from exc

    try:
        return ManagedRunStatus.model_validate(raw)
    except ValidationError as exc:
        raise RunStatusContractError(
            f"run_status.json at {p} failed Phase 2 contract validation: {exc}"
        ) from exc


def resolve_artifact_manifest_path(
    run_status: ManagedRunStatus,
    *,
    base_dir: Path | str | None = None,
) -> Path:
    """Resolve the artifact manifest path from a validated run_status.

    The path comes exclusively from run_status.artifact_manifest_path.
    No directory scanning or path inference is performed.

    Parameters
    ----------
    run_status:
        Validated ManagedRunStatus (from load_run_status_entrypoint).
    base_dir:
        Directory used to resolve relative paths.  Typically the managed
        repo root or the parent directory of run_status.json.
        Required when artifact_manifest_path is a relative path.

    Returns
    -------
    Path
        Absolute path to the artifact_manifest.json file.
        The file is not read or validated here.

    Raises
    ------
    ArtifactManifestPathMissingError
        artifact_manifest_path is None — contract violation for managed runs.
    ArtifactManifestPathResolutionError
        artifact_manifest_path is relative and base_dir was not supplied.
    """
    if run_status.artifact_manifest_path is None:
        raise ArtifactManifestPathMissingError(
            f"run_status for run_id={run_status.run_id!r} has no "
            "artifact_manifest_path. This is a contract violation for managed "
            "runs. The VF audit must be updated (Phase 5) to write this field."
        )

    p = Path(run_status.artifact_manifest_path)
    if p.is_absolute():
        return p

    if base_dir is None:
        raise ArtifactManifestPathResolutionError(
            f"artifact_manifest_path={str(p)!r} is a relative path but no "
            "base_dir was supplied. Pass base_dir= (managed repo root or "
            "run_status.json parent directory) to resolve the path."
        )

    return (Path(base_dir) / p).resolve()
