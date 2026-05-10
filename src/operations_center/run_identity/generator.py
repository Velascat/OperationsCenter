# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Run identity generation and ENV injection.

Public surface
--------------
generate_managed_run_identity(repo_id, audit_type, *, env_var, metadata)
    Create a ManagedRunIdentity with a freshly-generated run_id.

apply_run_identity_env(base_env, identity, *, allow_same)
    Inject identity.run_id into a copy of base_env under identity.env_var.

prepare_managed_audit_invocation(repo_id, audit_type, ...)
    End-to-end helper: generate identity → resolve Phase 3 request →
    inject env → return PreparedManagedAuditInvocation.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from operations_center.audit_toolset.commands import resolve_invocation_request
from operations_center.audit_toolset.contracts import ManagedAuditInvocationRequest
from operations_center.managed_repos.loader import load_managed_repo_config

from .errors import RunIdentityEnvConflictError
from .models import ManagedRunIdentity


def generate_managed_run_identity(
    repo_id: str,
    audit_type: str,
    *,
    env_var: str = "AUDIT_RUN_ID",
    metadata: dict[str, Any] | None = None,
    _now: datetime | None = None,
) -> ManagedRunIdentity:
    """Generate a fresh ManagedRunIdentity.

    Parameters
    ----------
    repo_id:
        Managed repo identifier (e.g. "example_managed_repo").
    audit_type:
        The audit type being invoked (e.g. "representative").
    env_var:
        The environment variable name to inject run_id through.
        Defaults to "AUDIT_RUN_ID"; read from managed repo config in the
        high-level helpers.
    metadata:
        Arbitrary key-value pairs forwarded to the identity record.
    _now:
        Overrides the current UTC time (testing only).

    Returns
    -------
    ManagedRunIdentity
        A validated identity record.  The run_id is unique by timestamp
        (second resolution) + 4 bytes of random entropy.

    Notes
    -----
    run_id format: {repo_id}_{audit_type}_{YYYYMMDDTHHMMSSz}_{8hex}
    Example:       example_managed_repo_audit_type_1_20260426T164233Z_a1b2c3d4
    """
    now = _now or datetime.now(tz=timezone.utc)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(4)
    run_id = f"{repo_id}_{audit_type}_{timestamp}_{suffix}"
    return ManagedRunIdentity(
        repo_id=repo_id,
        audit_type=audit_type,
        run_id=run_id,
        created_at=now,
        env_var=env_var,
        metadata=dict(metadata or {}),
    )


def generate_managed_run_identity_from_config(
    repo_id: str,
    audit_type: str,
    *,
    config_dir: Path | str | None = None,
    metadata: dict[str, Any] | None = None,
    _now: datetime | None = None,
) -> ManagedRunIdentity:
    """Generate a ManagedRunIdentity using env_var from managed repo config.

    Reads the configured ``run_id.env_var`` from the Phase 1 managed repo
    config so the env var name is always authoritative from config, not
    hardcoded at the call site.
    """
    config = load_managed_repo_config(repo_id, config_dir=config_dir)
    env_var = config.run_id.env_var
    return generate_managed_run_identity(
        repo_id,
        audit_type,
        env_var=env_var,
        metadata=metadata,
        _now=_now,
    )


def apply_run_identity_env(
    base_env: dict[str, str],
    identity: ManagedRunIdentity,
    *,
    allow_same: bool = True,
) -> dict[str, str]:
    """Inject run identity into a copy of base_env.

    Parameters
    ----------
    base_env:
        Existing environment variables.  Not mutated.
    identity:
        The run identity to inject.
    allow_same:
        If True (default), a pre-existing ``env_var`` value that already
        equals ``identity.run_id`` is accepted (idempotent injection).
        If False, any pre-existing value raises RunIdentityEnvConflictError.

    Returns
    -------
    dict[str, str]
        New dict with identity.env_var = identity.run_id added.

    Raises
    ------
    RunIdentityEnvConflictError
        base_env already contains identity.env_var set to a different value.
    """
    existing = base_env.get(identity.env_var)
    if existing is not None:
        if existing != identity.run_id:
            raise RunIdentityEnvConflictError(
                f"base_env already contains {identity.env_var}={existing!r} "
                f"which conflicts with identity run_id={identity.run_id!r}. "
                "Clear the existing value or use a fresh env."
            )
        if not allow_same:
            raise RunIdentityEnvConflictError(
                f"base_env already contains {identity.env_var}={existing!r}. "
                "allow_same=False was set; any pre-existing value is rejected."
            )
    result = dict(base_env)
    result[identity.env_var] = identity.run_id
    return result


class PreparedManagedAuditInvocation:
    """A resolved, ready-to-dispatch managed audit invocation.

    Holds both the run identity (for lifecycle tracking) and the Phase 3
    invocation request (for command dispatch).  Phase 6 consumes this.
    Nothing is executed here.
    """

    __slots__ = ("identity", "request", "metadata")

    def __init__(
        self,
        identity: ManagedRunIdentity,
        request: ManagedAuditInvocationRequest,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.identity = identity
        self.request = request
        self.metadata: dict[str, Any] = dict(metadata or {})

    def __repr__(self) -> str:
        return (
            "PreparedManagedAuditInvocation("
            f"repo_id={self.identity.repo_id!r}, "
            f"audit_type={self.identity.audit_type!r}, "
            f"run_id={self.identity.run_id!r})"
        )


def prepare_managed_audit_invocation(
    repo_id: str,
    audit_type: str,
    *,
    config_dir: Path | str | None = None,
    allow_not_yet_run: bool = True,
    extra_env: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
    _now: datetime | None = None,
) -> PreparedManagedAuditInvocation:
    """Prepare a managed audit invocation without executing it.

    Performs the full identity + invocation preparation flow:

    1. Generate ManagedRunIdentity (with env_var from config).
    2. Build env by injecting AUDIT_RUN_ID into extra_env.
    3. Resolve Phase 3 ManagedAuditInvocationRequest with the run_id.
    4. Return PreparedManagedAuditInvocation.

    Parameters
    ----------
    repo_id:
        Managed repo identifier.
    audit_type:
        Audit type to invoke.
    config_dir:
        Override for the managed repo config directory.
    allow_not_yet_run:
        Forwarded to Phase 3 command resolver.
    extra_env:
        Additional env vars merged in alongside AUDIT_RUN_ID.
    metadata:
        Arbitrary caller metadata forwarded to identity and invocation.
    _now:
        UTC datetime override for testing.

    Returns
    -------
    PreparedManagedAuditInvocation
        Contains .identity (ManagedRunIdentity) and .request
        (ManagedAuditInvocationRequest).  Nothing is executed.
    """
    identity = generate_managed_run_identity_from_config(
        repo_id,
        audit_type,
        config_dir=config_dir,
        metadata=metadata,
        _now=_now,
    )
    env = apply_run_identity_env(dict(extra_env or {}), identity)
    request = resolve_invocation_request(
        repo_id,
        audit_type,
        identity.run_id,
        config_dir=config_dir,
        allow_not_yet_run=allow_not_yet_run,
        extra_env=env,
        metadata=metadata,
    )
    return PreparedManagedAuditInvocation(
        identity=identity,
        request=request,
        metadata=dict(metadata or {}),
    )
