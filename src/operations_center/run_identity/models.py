# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ManagedRunIdentity — the identity record for a managed audit run.

Format
------
run_id follows a stable, path-safe, log-safe format:

    {repo_id}_{audit_type}_{timestamp}_{suffix}

Example:

    videofoundry_representative_20260426T164233Z_a1b2c3d4
    videofoundry_stack_authoring_20260426T164233Z_b5c6d7e8

Components:
    repo_id      lowercase alphanumeric + underscore
    audit_type   lowercase alphanumeric + underscore
    timestamp    UTC datetime in YYYYMMDDTHHMMSSz format
    suffix       8 lowercase hex chars from secrets.token_hex(4)

The format guarantees:
    - Path-safe: only [a-z0-9_] chars
    - Log-safe: printable ASCII, no shell-special chars
    - JSON-safe: valid JSON string value
    - Unique: timestamp (second resolution) + 4 bytes of random entropy
    - Traceable: repo_id and audit_type embedded for human readability
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

_RUN_ID_PATTERN = re.compile(
    r"^[a-z0-9_]+"        # repo_id (one or more chars)
    r"_[a-z0-9_]+"        # _audit_type (one or more chars)
    r"_\d{8}T\d{6}Z"      # _YYYYMMDDTHHMMSSz
    r"_[a-f0-9]{8}$"      # _xxxxxxxx (8 hex chars)
)


def is_valid_run_id(run_id: str) -> bool:
    """Return True if run_id matches the managed-run format."""
    return bool(_RUN_ID_PATTERN.match(run_id))


class ManagedRunIdentity(BaseModel):
    """Identity record for a single managed audit run.

    OperationsCenter creates this before invoking any managed repo command.
    The run_id is propagated via the env_var into the subprocess environment.
    """

    repo_id: str
    audit_type: str
    run_id: str
    created_at: datetime
    env_var: str = "AUDIT_RUN_ID"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("repo_id")
    @classmethod
    def _repo_id_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("repo_id must not be empty")
        return v

    @field_validator("audit_type")
    @classmethod
    def _audit_type_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("audit_type must not be empty")
        return v

    @field_validator("run_id")
    @classmethod
    def _run_id_format(cls, v: str) -> str:
        if not is_valid_run_id(v):
            raise ValueError(
                f"run_id {v!r} does not match the managed-run format "
                f"'{'{repo_id}_{audit_type}_{YYYYMMDDTHHMMSSz}_{8hex}'}'. "
                f"Use generate_managed_run_identity() to create a valid id."
            )
        return v

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware (UTC)")
        return v
