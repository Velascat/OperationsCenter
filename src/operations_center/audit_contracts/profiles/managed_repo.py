# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
managed_repo.py — example managed-repo producer profile for the managed-repo audit contract.

This module ships the example producer profile shape (data values
belong in private config — see PR-C); it is not the contract itself.
Generic contract fields are defined in audit_contracts.run_status and
audit_contracts.artifact_manifest. This profile captures only what is
specific to one bound managed repo.

Another managed repo would create its own profile module without touching
the generic contract.

Phase 0 ground truth drives all values here. See:
  docs/architecture/managed-repos/audit_ground_truth.md
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..vocabulary import (
    Limitation,
    ExampleManagedRepoArtifactKind,
    ExampleManagedRepoAuditType,
    ExampleManagedRepoSourceStage,
)


class ManagedRepoAuditTypeSpec(BaseModel):
    """Per-audit-type metadata for a managed repo."""
    audit_type: str
    output_dir: str
    run_status_finalization: bool
    phase_0_evidence: str
    limitations: list[Limitation] = Field(default_factory=list)
    notes: str = ""

    model_config = {"frozen": True}


class ManagedRepoPathQuirk(BaseModel):
    """A known path-layout quirk specific to one bound managed repo."""
    description: str
    example: str | None = None

    model_config = {"frozen": True}


class ManagedRepoAuditProfile(BaseModel):
    """Managed-repo-specific metadata for the managed-repo audit contract.

    This profile is consumed by OpsCenter tooling that needs to interpret
    the bound managed repo's artifacts. It does NOT redefine the generic contract.

    Generic contract: audit_contracts.run_status, audit_contracts.artifact_manifest
    This profile: managed-repo-specific assumptions, quirks, Phase 0 gaps.
    """

    producer_id: str = "example_managed_repo"
    contract_version: str = "1.0"

    # Supported audit types with per-type metadata
    audit_type_specs: list[ManagedRepoAuditTypeSpec] = Field(default_factory=list)

    # Known source stage names (from Phase 0 ground truth)
    known_source_stages: list[str] = Field(default_factory=list)

    # Known artifact kind vocabulary
    known_artifact_kinds: list[str] = Field(default_factory=list)

    # Path quirks specific to the bound managed repo
    path_quirks: list[ManagedRepoPathQuirk] = Field(default_factory=list)

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
        "Audit types whose producer never writes a final run_status.json "
        "have run_status_finalization=False. OpsCenter readers must accept "
        "the legacy 'in_progress' value as terminal for those types until "
        "the producer adds finalization logic."
    )
    legacy_status_value: str = (
        "Pre-Phase-5 producers emit 'in_progress' instead of 'running'. "
        "Phase 5 must switch to 'running'. "
        "OpsCenter readers must accept 'in_progress' during transition."
    )

    # Infrastructure noise: paths that must be excluded from manifests
    excluded_path_patterns: list[str] = Field(
        default_factory=list,
        description="Glob patterns for infrastructure files to exclude.",
    )

    # Bucket naming convention. Real bindings substitute their own slug
    # vocabulary in place of `producer_slug` (e.g. tenant id, channel id,
    # workspace name) — whatever uniquely identifies a producer run.
    bucket_naming_pattern: str = "{producer_slug}_{YYYYMMDD}_{HHMMSS}_{run_id_hex}"
    bucket_naming_note: str = (
        "producer_slug is whatever the bound managed repo uses to disambiguate "
        "concurrent runs. run_id_hex is uuid4().hex (32 chars, no dashes)."
    )

    model_config = {"frozen": True}

    def get_audit_type_spec(self, audit_type: str) -> ManagedRepoAuditTypeSpec | None:
        return next((s for s in self.audit_type_specs if s.audit_type == audit_type), None)

    @property
    def audit_types_with_finalization(self) -> list[str]:
        return [s.audit_type for s in self.audit_type_specs if s.run_status_finalization]

    @property
    def audit_types_without_finalization(self) -> list[str]:
        return [s.audit_type for s in self.audit_type_specs if not s.run_status_finalization]


# ---------------------------------------------------------------------------
# Example managed-repo profile instance
# ---------------------------------------------------------------------------

EXAMPLE_MANAGED_REPO_PROFILE = ManagedRepoAuditProfile(
    audit_type_specs=[
        ManagedRepoAuditTypeSpec(
            audit_type=ExampleManagedRepoAuditType.AUDIT_TYPE_1.value,
            output_dir="tools/audit/report/audit_type_1",
            run_status_finalization=True,
            phase_0_evidence="example_finalized_audit_type",
            limitations=[],
            notes=(
                "Example audit type that finalizes run_status.json. Real bindings "
                "set this to true for any audit that emits a terminal status."
            ),
        ),
        ManagedRepoAuditTypeSpec(
            audit_type=ExampleManagedRepoAuditType.AUDIT_TYPE_2.value,
            output_dir="tools/audit/report/audit_type_2",
            run_status_finalization=False,
            phase_0_evidence="example_unfinalized_audit_type",
            limitations=[Limitation.PRODUCER_NOT_FINALIZED],
            notes=(
                "Example audit type that does not finalize run_status.json. "
                "OpsCenter readers must accept in_progress as terminal until "
                "the producer adds finalization."
            ),
        ),
    ],
    known_source_stages=[s.value for s in ExampleManagedRepoSourceStage],
    known_artifact_kinds=[k.value for k in ExampleManagedRepoArtifactKind],
    path_quirks=[
        ManagedRepoPathQuirk(
            description=(
                "Path layout may be non-uniform — artifacts can land in run_root, "
                "artifacts subdirectories, or named subdirs depending on producer "
                "stage. Real bindings document the specific layout the bound "
                "managed repo produces."
            ),
            example=(
                "run_root: bucket_top_level.json | "
                "artifacts_subdir: artifacts/<stage>/<file>.json | "
                "audit_subdir: audit/<file>.txt"
            ),
        ),
        ManagedRepoPathQuirk(
            description=(
                "architecture_invariants is a repo-level singleton written outside any "
                "per-run bucket. It has no run_id and is overwritten in-place."
            ),
            example="tools/audit/report/architecture_invariants/latest.json",
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


__all__ = ["ManagedRepoAuditProfile", "ManagedRepoAuditTypeSpec", "EXAMPLE_MANAGED_REPO_PROFILE"]
