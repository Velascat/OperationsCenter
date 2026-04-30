# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Run identity generation and ENV injection for managed repo audits.

Phase 4 of the OpsCenter ↔ VideoFoundry audit system.

Public surface
--------------
models.ManagedRunIdentity
    Identity record for a managed audit run.  Carries run_id, env_var,
    created_at, and traceable repo/audit metadata.

generator.generate_managed_run_identity
    Generate a fresh ManagedRunIdentity from explicit arguments.

generator.generate_managed_run_identity_from_config
    Generate a ManagedRunIdentity reading env_var from Phase 1 config.

generator.apply_run_identity_env
    Inject run_id into a copy of an env dict; explicit conflict policy.

generator.prepare_managed_audit_invocation
    End-to-end helper: identity → Phase 3 request → PreparedManagedAuditInvocation.

generator.PreparedManagedAuditInvocation
    Holds identity + request ready for Phase 6 dispatch.

errors.*
    Explicit error types for identity violations.
"""

from .errors import RunIdentityEnvConflictError, RunIdentityError, RunIdentityFormatError
from .generator import (
    PreparedManagedAuditInvocation,
    apply_run_identity_env,
    generate_managed_run_identity,
    generate_managed_run_identity_from_config,
    prepare_managed_audit_invocation,
)
from .models import ManagedRunIdentity, is_valid_run_id

__all__ = [
    "ManagedRunIdentity",
    "is_valid_run_id",
    "generate_managed_run_identity",
    "generate_managed_run_identity_from_config",
    "apply_run_identity_env",
    "PreparedManagedAuditInvocation",
    "prepare_managed_audit_invocation",
    "RunIdentityError",
    "RunIdentityFormatError",
    "RunIdentityEnvConflictError",
]
