# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
videofoundry.py — VideoFoundry producer profile for the managed-repo audit contract.

VideoFoundry is the FIRST producer profile, not the contract itself.
Generic contract fields are defined in audit_contracts.run_status and
audit_contracts.artifact_manifest. This profile captures only what is
specific to VideoFoundry.

Another managed repo would create its own profile module without touching
the generic contract.

Phase 0 ground truth drives all values here. See:
  docs/architecture/videofoundry_audit_ground_truth.md
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..vocabulary import (
    Limitation,
    VideoFoundryArtifactKind,
    VideoFoundryAuditType,
    VideoFoundrySourceStage,
)


class VideoFoundryAuditTypeSpec(BaseModel):
    """Per-audit-type metadata for VideoFoundry."""
    audit_type: str
    output_dir: str
    run_status_finalization: bool
    phase_0_evidence: str
    limitations: list[Limitation] = Field(default_factory=list)
    notes: str = ""

    model_config = {"frozen": True}


class VideoFoundryPathQuirk(BaseModel):
    """A known path-layout quirk specific to VideoFoundry."""
    description: str
    example: str | None = None

    model_config = {"frozen": True}


class VideoFoundryProducerProfile(BaseModel):
    """VideoFoundry-specific metadata for the managed-repo audit contract.

    This profile is consumed by OpsCenter tooling that needs to interpret
    VideoFoundry artifacts. It does NOT redefine the generic contract.

    Generic contract: audit_contracts.run_status, audit_contracts.artifact_manifest
    This profile: VideoFoundry-only assumptions, quirks, Phase 0 gaps.
    """

    producer_id: str = "videofoundry"
    contract_version: str = "1.0"

    # Supported audit types with per-type metadata
    audit_type_specs: list[VideoFoundryAuditTypeSpec] = Field(default_factory=list)

    # Known source stage names (from Phase 0 ground truth)
    known_source_stages: list[str] = Field(default_factory=list)

    # Known artifact kind vocabulary
    known_artifact_kinds: list[str] = Field(default_factory=list)

    # Path quirks specific to VideoFoundry
    path_quirks: list[VideoFoundryPathQuirk] = Field(default_factory=list)

    # Repository singleton: architecture_invariants
    architecture_invariants_singleton_path: str = (
        "tools/audit/report/architecture_invariants/latest.json"
    )
    architecture_invariants_note: str = (
        "Not inside any per-run bucket. No run_id. Overwritten in-place on each run. "
        "Must be represented as location=repo_singleton with valid_for=[latest_snapshot] "
        "and limitations=[repo_singleton_overwritten]."
    )

    # Phase 0 documented gaps — must be reflected in manifests until fixed
    run_status_finalization_gap: str = (
        "Five of six audit types (enrichment, ideation, render, segmentation, "
        "stack_authoring) use prepare_audit_bucket() which writes initial "
        "'in_progress' but never writes a final status. "
        "Phase 5 must add finalization logic to all five types."
    )
    legacy_status_value: str = (
        "VideoFoundry currently emits 'in_progress' instead of 'running'. "
        "Phase 5 must switch to 'running'. "
        "OpsCenter readers must accept 'in_progress' during transition."
    )

    # Infrastructure noise: paths that must be excluded from manifests
    excluded_path_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns for infrastructure files to exclude.",
    )

    # Bucket naming convention
    bucket_naming_pattern: str = "{channel_slug}_{YYYYMMDD}_{HHMMSS}_{run_id_hex}"
    bucket_naming_note: str = (
        "channel_slug is the sanitized YouTube channel name. "
        "run_id_hex is uuid4().hex (32 chars, no dashes)."
    )

    model_config = {"frozen": True}

    def get_audit_type_spec(self, audit_type: str) -> VideoFoundryAuditTypeSpec | None:
        return next((s for s in self.audit_type_specs if s.audit_type == audit_type), None)

    @property
    def audit_types_with_finalization(self) -> list[str]:
        return [s.audit_type for s in self.audit_type_specs if s.run_status_finalization]

    @property
    def audit_types_without_finalization(self) -> list[str]:
        return [s.audit_type for s in self.audit_type_specs if not s.run_status_finalization]


# ---------------------------------------------------------------------------
# Canonical VideoFoundry profile instance
# ---------------------------------------------------------------------------

VIDEOFOUNDRY_PROFILE = VideoFoundryProducerProfile(
    audit_type_specs=[
        VideoFoundryAuditTypeSpec(
            audit_type=VideoFoundryAuditType.REPRESENTATIVE.value,
            output_dir="tools/audit/report/representative",
            run_status_finalization=True,
            phase_0_evidence="one_run_inspected_in_progress",
            limitations=[
                Limitation.PARTIAL_RUN,
                Limitation.PATH_LAYOUT_NON_UNIFORM,
            ],
            notes=(
                "One real run inspected (interrupted during rendering). "
                "Authoring artifacts confirmed present. "
                "Delivery/post-run artifacts absent from inspected run."
            ),
        ),
        VideoFoundryAuditTypeSpec(
            audit_type=VideoFoundryAuditType.ENRICHMENT.value,
            output_dir="tools/audit/report/enrichment",
            run_status_finalization=False,
            phase_0_evidence="source_inspected_no_run",
            limitations=[
                Limitation.NON_REPRESENTATIVE_AUDIT_UNVERIFIED,
                Limitation.PRODUCER_NOT_FINALIZED,
            ],
            notes="No runs available. run_status.json never finalized.",
        ),
        VideoFoundryAuditTypeSpec(
            audit_type=VideoFoundryAuditType.IDEATION.value,
            output_dir="tools/audit/report/ideation",
            run_status_finalization=False,
            phase_0_evidence="source_inspected_no_run",
            limitations=[
                Limitation.NON_REPRESENTATIVE_AUDIT_UNVERIFIED,
                Limitation.PRODUCER_NOT_FINALIZED,
            ],
            notes="No runs available. run_status.json never finalized.",
        ),
        VideoFoundryAuditTypeSpec(
            audit_type=VideoFoundryAuditType.RENDER.value,
            output_dir="tools/audit/report/render",
            run_status_finalization=False,
            phase_0_evidence="source_inspected_no_run",
            limitations=[
                Limitation.NON_REPRESENTATIVE_AUDIT_UNVERIFIED,
                Limitation.PRODUCER_NOT_FINALIZED,
            ],
            notes="No runs available. run_status.json never finalized.",
        ),
        VideoFoundryAuditTypeSpec(
            audit_type=VideoFoundryAuditType.SEGMENTATION.value,
            output_dir="tools/audit/report/segmentation",
            run_status_finalization=False,
            phase_0_evidence="source_inspected_no_run",
            limitations=[
                Limitation.NON_REPRESENTATIVE_AUDIT_UNVERIFIED,
                Limitation.PRODUCER_NOT_FINALIZED,
            ],
            notes="No runs available. run_status.json never finalized.",
        ),
        VideoFoundryAuditTypeSpec(
            audit_type=VideoFoundryAuditType.STACK_AUTHORING.value,
            # NOTE: directory name is "authoring", not "stack_authoring" (Phase 0 finding)
            output_dir="tools/audit/report/authoring",
            run_status_finalization=False,
            phase_0_evidence="source_inspected_no_run",
            limitations=[
                Limitation.NON_REPRESENTATIVE_AUDIT_UNVERIFIED,
                Limitation.PRODUCER_NOT_FINALIZED,
            ],
            notes=(
                "No runs available. Directory is 'authoring' not 'stack_authoring'. "
                "run_status.json never finalized."
            ),
        ),
    ],
    known_source_stages=[s.value for s in VideoFoundrySourceStage],
    known_artifact_kinds=[k.value for k in VideoFoundryArtifactKind],
    path_quirks=[
        VideoFoundryPathQuirk(
            description=(
                "Artifacts land in at least four distinct locations relative to the bucket: "
                "run_root, artifacts/authoring/, artifacts/script_contract/ScriptWriting/, "
                "audit/, and text_overlay/. Path layout is non-uniform across audit types."
            ),
            example=(
                "run_root: Connective_Contours_topic_selection.json | "
                "artifacts_subdir: artifacts/authoring/Connective_Contours_evidence_pack.json | "
                "audit_subdir: audit/Connective_Contours_audit__voicesamplecleanstage__deadcode_report.txt"
            ),
        ),
        VideoFoundryPathQuirk(
            description=(
                "stack_authoring output directory is named 'authoring', not 'stack_authoring'. "
                "The CLI name and the directory name do not match."
            ),
            example="tools/audit/report/authoring/ (not stack_authoring/)",
        ),
        VideoFoundryPathQuirk(
            description=(
                "architecture_invariants is a repo-level singleton written outside any "
                "per-run bucket. It has no run_id and is overwritten in-place."
            ),
            example="tools/audit/report/architecture_invariants/latest.json",
        ),
        VideoFoundryPathQuirk(
            description=(
                "ASR observation paths in voice_over_asr_observations.jsonl reference "
                "files in VideoFoundry/temp/voice_over_delivery/, not in the audit bucket. "
                "These are transient delivery files, not stable audit artifacts."
            ),
        ),
        VideoFoundryPathQuirk(
            description=(
                "Large script object files (script_object__*.json) may be 1.5–1.6 MB. "
                "The manifest records paths only; it must not embed content."
            ),
        ),
    ],
    excluded_path_patterns=[
        "coverage.ini",
        ".coverage*",
        "sitecustomize.py",
        "__pycache__/**",
        "*.pyc",
    ],
)


__all__ = ["VideoFoundryProducerProfile", "VideoFoundryAuditTypeSpec", "VIDEOFOUNDRY_PROFILE"]
