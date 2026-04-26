"""Explicit error types for the audit toolset contract layer."""

from __future__ import annotations


class ManagedAuditToolsetError(Exception):
    """Base for all audit toolset contract violations."""


class ManagedRepoNotFoundError(ManagedAuditToolsetError):
    """No managed repo config exists for the given repo_id."""


class ManagedRepoCapabilityError(ManagedAuditToolsetError):
    """The managed repo does not advertise the required capability."""


class ManagedAuditTypeUnsupportedError(ManagedAuditToolsetError):
    """The requested audit_type is not declared in the managed repo config."""


class ManagedAuditCommandUnavailableError(ManagedAuditToolsetError):
    """The audit command has a status that blocks invocation contract generation.

    Raised for command_status values of 'unknown' or 'needs_confirmation'.
    """


class RunStatusNotFoundError(ManagedAuditToolsetError):
    """run_status.json was not found at the declared path."""


class RunStatusContractError(ManagedAuditToolsetError):
    """run_status.json failed Phase 2 contract validation."""


class ArtifactManifestPathMissingError(ManagedAuditToolsetError):
    """artifact_manifest_path is absent from run_status.json.

    This is a contract violation for managed runs post-Phase-5.
    """


class ArtifactManifestPathResolutionError(ManagedAuditToolsetError):
    """artifact_manifest_path could not be resolved to a usable path.

    Raised when the path is relative and no base_dir was supplied.
    """
