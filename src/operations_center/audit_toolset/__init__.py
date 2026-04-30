# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Audit toolset contract layer for managed repo audits.

Phase 3 of the OpsCenter ↔ VideoFoundry audit system.

Public surface
--------------
contracts.ManagedAuditInvocationRequest
    The validated request shape that Phase 6 dispatch will consume.

commands.resolve_invocation_request
    Resolve an invocation request from managed repo config.

discovery.load_run_status_entrypoint
    Read and validate run_status.json (Phase 2 contract models).

discovery.resolve_artifact_manifest_path
    Resolve artifact_manifest_path from a validated run_status.

errors.*
    Explicit error types for every contract violation.
"""

from .commands import resolve_invocation_request
from .contracts import ManagedAuditInvocationRequest
from .discovery import load_run_status_entrypoint, resolve_artifact_manifest_path
from .errors import (
    ArtifactManifestPathMissingError,
    ArtifactManifestPathResolutionError,
    ManagedAuditCommandUnavailableError,
    ManagedAuditToolsetError,
    ManagedAuditTypeUnsupportedError,
    ManagedRepoCapabilityError,
    ManagedRepoNotFoundError,
    RunStatusContractError,
    RunStatusNotFoundError,
)

__all__ = [
    "ManagedAuditInvocationRequest",
    "resolve_invocation_request",
    "load_run_status_entrypoint",
    "resolve_artifact_manifest_path",
    "ManagedAuditToolsetError",
    "ManagedRepoNotFoundError",
    "ManagedRepoCapabilityError",
    "ManagedAuditTypeUnsupportedError",
    "ManagedAuditCommandUnavailableError",
    "RunStatusNotFoundError",
    "RunStatusContractError",
    "ArtifactManifestPathMissingError",
    "ArtifactManifestPathResolutionError",
]
