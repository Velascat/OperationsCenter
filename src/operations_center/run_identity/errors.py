# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Error types for the run identity layer."""

from __future__ import annotations


class RunIdentityError(Exception):
    """Base for all run identity errors."""


class RunIdentityFormatError(RunIdentityError):
    """A supplied run_id does not match the expected format."""


class RunIdentityEnvConflictError(RunIdentityError):
    """AUDIT_RUN_ID already exists in the env with a different value.

    Raised by apply_run_identity_env when the caller's base_env already
    contains the env var set to a value that does not match the identity.
    This prevents silently overwriting a run_id that came from a different
    invocation context.
    """
