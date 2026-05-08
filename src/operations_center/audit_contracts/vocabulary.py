# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
vocabulary.py — controlled vocabulary for the managed-repo audit contract.

Two layers are defined here:

  Generic layer — reusable by any managed repo.
  Example managed-repo profile layer — bound-repo values, clearly namespaced.

All enums are str-based so they round-trip cleanly through JSON.
"""

from __future__ import annotations

from enum import Enum


# ---------------------------------------------------------------------------
# Generic managed-repo audit vocabulary
# ---------------------------------------------------------------------------

class RunStatus(str, Enum):
    """Lifecycle status of a managed audit run.

    Canonical values for run_status.json and artifact manifests.

    Decision: pre-Phase-5 producers emit "in_progress" (legacy).
    The contract canonicalises this to RUNNING. Phase 5 must switch
    producers to emit "running". IN_PROGRESS_LEGACY is accepted
    during the transition period by readers but must not be emitted
    by compliant producers.
    """
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"
    # Legacy: pre-Phase-5 producers emit this instead of "running".
    # Readers must accept it; producers must not emit it after Phase 5.
    IN_PROGRESS_LEGACY = "in_progress"


class ManifestStatus(str, Enum):
    """Lifecycle status of the artifact manifest itself.

    Distinct from RunStatus — the manifest can be partial even when
    the run has completed (e.g. some artifact writes failed).
    """
    INITIALIZING = "initializing"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Location(str, Enum):
    """Path-layout class for an artifact.

    Describes WHERE in the file system the artifact lives relative
    to the per-run bucket, not what the artifact IS.

    Do not collapse REPO_SINGLETON into per-run locations —
    architecture-invariant outputs have no per-run scope.
    """
    RUN_ROOT = "run_root"               # top-level inside the per-run bucket
    ARTIFACTS_SUBDIR = "artifacts_subdir"     # under artifacts/ inside the bucket
    AUDIT_SUBDIR = "audit_subdir"             # under audit/ inside the bucket
    TEXT_OVERLAY_SUBDIR = "text_overlay_subdir"  # under text_overlay/ inside the bucket
    REPO_SINGLETON = "repo_singleton"         # fixed path outside any per-run bucket
    EXTERNAL_OR_UNKNOWN = "external_or_unknown"  # path not classifiable above


class PathRole(str, Enum):
    """Semantic role of an artifact path."""
    PRIMARY = "primary"       # main machine-readable output for a stage
    SUMMARY = "summary"       # aggregate/summary view
    DETAIL = "detail"         # per-item detail record
    GATE = "gate"             # pass/fail gate verdict
    TRACE = "trace"           # execution trace or log
    METADATA = "metadata"     # run or system metadata
    NOISE = "noise"           # infrastructure file that should be excluded
    UNKNOWN = "unknown"


class ContentType(str, Enum):
    """MIME-like content type for an artifact."""
    JSON = "application/json"
    JSONL = "application/x-ndjson"
    TEXT = "text/plain"
    MARKDOWN = "text/markdown"
    BINARY = "application/octet-stream"
    UNKNOWN = "unknown"


class ArtifactStatus(str, Enum):
    """Whether an expected artifact is present or missing."""
    PRESENT = "present"
    MISSING = "missing"
    EXPECTED = "expected"   # declared but not yet produced (run still in progress)


class ConsumerType(str, Enum):
    """Who or what may consume an artifact."""
    HUMAN_REVIEW = "human_review"
    AUTOMATED_ANALYSIS = "automated_analysis"
    FIXTURE_HARVESTING = "fixture_harvesting"
    SLICE_REPLAY = "slice_replay"
    REGRESSION_TESTING = "regression_testing"
    ARCHITECTURE_INVARIANT_VERIFICATION = "architecture_invariant_verification"
    FAILURE_DIAGNOSIS = "failure_diagnosis"
    UNKNOWN = "unknown"


class ValidFor(str, Enum):
    """Temporal/contextual scope for which an artifact is meaningful."""
    CURRENT_RUN_ONLY = "current_run_only"
    CROSS_RUN_COMPARISON = "cross_run_comparison"
    LATEST_SNAPSHOT = "latest_snapshot"
    HISTORICAL_RECORD = "historical_record"
    PARTIAL_RUN_ANALYSIS = "partial_run_analysis"
    UNKNOWN = "unknown"


class Limitation(str, Enum):
    """Known limitations or caveats on an artifact or manifest."""
    PARTIAL_RUN = "partial_run"
    MISSING_DOWNSTREAM_ARTIFACTS = "missing_downstream_artifacts"
    PRODUCER_NOT_FINALIZED = "producer_not_finalized"
    NON_REPRESENTATIVE_AUDIT_UNVERIFIED = "non_representative_audit_unverified"
    REPO_SINGLETON_OVERWRITTEN = "repo_singleton_overwritten"
    INFRASTRUCTURE_NOISE_EXCLUDED = "infrastructure_noise_excluded"
    PATH_LAYOUT_NON_UNIFORM = "path_layout_non_uniform"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Example managed-repo profile vocabulary
#
# These values describe what the example managed repo currently produces.
# They are not part of the generic managed-repo contract.
# Future managed repos define their own profile vocabulary.
# ---------------------------------------------------------------------------

class ExampleManagedRepoAuditType(str, Enum):
    """The six audit types supported by the example managed repo."""
    REPRESENTATIVE = "representative"
    ENRICHMENT = "enrichment"
    IDEATION = "ideation"
    RENDER = "render"
    SEGMENTATION = "segmentation"
    STACK_AUTHORING = "stack_authoring"


class ExampleManagedRepoSourceStage(str, Enum):
    """Stage names observed in the example managed repo's audit output (Phase 0 ground truth)."""
    TOPIC_SELECTION = "TopicSelectionStage"
    OUTLINE_PLANNING = "OutlinePlanningStage"
    SCRIPT_WRITING = "ScriptWritingStage"
    SCRIPT_SEGMENTATION = "ScriptSegmentationStage"
    SCRIPT_ENRICHMENT = "ScriptEnrichmentStage"
    FACT_CHECK = "FactCheckStage"
    STRUCTURED_FACT_CHECK = "StructuredFactCheckStage"
    AUTHOR_PROSODY = "AuthorProsodyStage"
    VOICE_OVER = "VoiceOverStage"
    VOICE_SAMPLE_CLEAN = "VoiceSampleCleanStage"
    LIFECYCLE = "lifecycle"
    POST_RUN = "post_run"
    ARCHITECTURE_INVARIANTS = "architecture_invariants"
    UNKNOWN = "unknown"


class ExampleManagedRepoArtifactKind(str, Enum):
    """Artifact kind vocabulary for the example managed repo's audit outputs.

    Based on Phase 0 ground truth. Not exhaustive — unknown artifacts
    should use UNKNOWN rather than fail.
    """
    RUN_STATUS = "run_status"
    STAGE_REPORT = "stage_report"
    AUDIT_REPORT = "audit_report"
    JSON_REPORT = "json_report"
    JSONL_REPORT = "jsonl_report"
    TEXT_REPORT = "text_report"
    SCRIPT_CONTRACT = "script_contract"
    SCRIPT_ARTIFACT = "script_artifact"
    AUTHORING_ARTIFACT = "authoring_artifact"
    RENDERING_ARTIFACT = "rendering_artifact"
    ALIGNMENT_ARTIFACT = "alignment_artifact"
    TIMING_ARTIFACT = "timing_artifact"
    VISUAL_ARTIFACT = "visual_artifact"
    ARCHITECTURE_INVARIANT = "architecture_invariant"
    LOG = "log"
    TRACEBACK = "traceback"
    METADATA = "metadata"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

GENERIC_ENUMS = (
    RunStatus, ManifestStatus, Location, PathRole, ContentType,
    ArtifactStatus, ConsumerType, ValidFor, Limitation,
)

EXAMPLE_MANAGED_REPO_PROFILE_ENUMS = (
    ExampleManagedRepoAuditType, ExampleManagedRepoSourceStage, ExampleManagedRepoArtifactKind,
)

__all__ = [
    # generic
    "RunStatus",
    "ManifestStatus",
    "Location",
    "PathRole",
    "ContentType",
    "ArtifactStatus",
    "ConsumerType",
    "ValidFor",
    "Limitation",
    "GENERIC_ENUMS",
    # example managed-repo profile
    "ExampleManagedRepoAuditType",
    "ExampleManagedRepoSourceStage",
    "ExampleManagedRepoArtifactKind",
    "EXAMPLE_MANAGED_REPO_PROFILE_ENUMS",
]
