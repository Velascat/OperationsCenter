# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Pydantic models for managed repo contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class RunIdInjection(BaseModel):
    source: Literal["operations_center"]
    env_var: str
    format: str = "uuid_hex"
    required_for_managed_runs: bool = True


class AuditDiscoveryStep(BaseModel):
    file: str
    field: str | None = None
    status: Literal["planned", "available", "not_yet_available"]
    phase_required: str | None = None


class AuditOutputDiscovery(BaseModel):
    entry_point: str
    chain: list[AuditDiscoveryStep] = Field(default_factory=list)
    notes: str = ""


class AuditType(BaseModel):
    audit_type: str
    command: str
    command_status: Literal["verified", "not_yet_run", "unknown", "needs_confirmation"]
    working_dir: str = "."
    env_injected: list[str] = Field(default_factory=list)
    output_dir: str
    status_file: str
    run_status_finalization: bool
    phases_from_source: list[str] = Field(default_factory=list)
    status_values: list[str] = Field(default_factory=list)
    evidence: str = ""
    notes: str = ""


class ManagedRepoAuditCapability(BaseModel):
    output_discovery: AuditOutputDiscovery
    bucket_naming: str = ""
    audit_types: list[AuditType] = Field(default_factory=list)

    def get_audit_type(self, name: str) -> AuditType | None:
        for at in self.audit_types:
            if at.audit_type == name:
                return at
        return None


class BoundaryPolicy(BaseModel):
    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class ManagedRepoConfig(BaseModel):
    repo_id: str
    repo_name: str
    repo_root: str
    run_id: RunIdInjection
    capabilities: list[str] = Field(default_factory=list)
    audit: ManagedRepoAuditCapability | None = None
    boundary: BoundaryPolicy = Field(default_factory=BoundaryPolicy)

    @model_validator(mode="after")
    def _check_semantic_constraints(self) -> "ManagedRepoConfig":
        if "audit" in self.capabilities and self.audit is None:
            raise ValueError(
                "capabilities includes 'audit' but audit field is absent — "
                "add an audit: block or remove 'audit' from capabilities"
            )
        if self.audit is not None and not self.audit.audit_types:
            raise ValueError(
                "audit field is present but audit_types is empty — "
                "add at least one audit_type entry"
            )
        if self.audit is None and "audit" not in self.capabilities:
            pass  # no audit at all — valid
        if not self.repo_id.strip():
            raise ValueError("repo_id must not be blank")
        if not self.repo_name.strip():
            raise ValueError("repo_name must not be blank")
        return self

    def has_capability(self, name: str) -> bool:
        return name in self.capabilities

    @property
    def audit_type_names(self) -> list[str]:
        if self.audit is None:
            return []
        return [at.audit_type for at in self.audit.audit_types]
