# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""
audit_contracts — managed-repo audit contract definitions for OperationsCenter.

Two layers:

  Generic layer (reusable by any managed repo):
    vocabulary  — controlled enums
    run_status  — ManagedRunStatus (run_status.json model)
    artifact_manifest — ManagedArtifactManifest, ManagedArtifactEntry, ExcludedPath

  Example managed-repo producer profile (data values move to private config):
    profiles.managed_repo — ManagedRepoAuditProfile, EXAMPLE_MANAGED_REPO_PROFILE

Contract version: 1.0
"""

from .artifact_manifest import (
    ExcludedPath,
    ManagedArtifactEntry,
    ManagedArtifactManifest,
)
from .run_status import ManagedRunStatus
from .vocabulary import (
    ArtifactStatus,
    ConsumerType,
    GENERIC_ENUMS,
    Limitation,
    Location,
    ManifestStatus,
    PathRole,
    RunStatus,
    ValidFor,
    ExampleManagedRepoArtifactKind,
    ExampleManagedRepoAuditType,
    ExampleManagedRepoSourceStage,
    EXAMPLE_MANAGED_REPO_PROFILE_ENUMS,
)

CONTRACT_VERSION = "1.0"
CONTRACT_NAME = "managed-repo-audit"

__all__ = [
    # generic models
    "ManagedRunStatus",
    "ManagedArtifactManifest",
    "ManagedArtifactEntry",
    "ExcludedPath",
    # generic vocabulary
    "RunStatus",
    "ManifestStatus",
    "Location",
    "PathRole",
    "ArtifactStatus",
    "ConsumerType",
    "ValidFor",
    "Limitation",
    "GENERIC_ENUMS",
    # example managed-repo profile vocabulary
    "ExampleManagedRepoAuditType",
    "ExampleManagedRepoSourceStage",
    "ExampleManagedRepoArtifactKind",
    "EXAMPLE_MANAGED_REPO_PROFILE_ENUMS",
    # constants
    "CONTRACT_VERSION",
    "CONTRACT_NAME",
]
