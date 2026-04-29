# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""
audit_contracts — managed-repo audit contract definitions for OperationsCenter.

Two layers:

  Generic layer (reusable by any managed repo):
    vocabulary  — controlled enums
    run_status  — ManagedRunStatus (run_status.json model)
    artifact_manifest — ManagedArtifactManifest, ManagedArtifactEntry, ExcludedPath

  VideoFoundry producer profile (VF-specific, not the contract itself):
    profiles.videofoundry — VideoFoundryProducerProfile, VIDEOFOUNDRY_PROFILE

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
    VideoFoundryArtifactKind,
    VideoFoundryAuditType,
    VideoFoundrySourceStage,
    VIDEOFOUNDRY_PROFILE_ENUMS,
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
    # videofoundry profile vocabulary
    "VideoFoundryAuditType",
    "VideoFoundrySourceStage",
    "VideoFoundryArtifactKind",
    "VIDEOFOUNDRY_PROFILE_ENUMS",
    # constants
    "CONTRACT_VERSION",
    "CONTRACT_NAME",
]
