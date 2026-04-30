# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Managed repo contracts — external repos OpsCenter invokes without importing."""

from .loader import load_managed_repo_config
from .models import (
    AuditOutputDiscovery,
    AuditType,
    BoundaryPolicy,
    ManagedRepoAuditCapability,
    ManagedRepoConfig,
    RunIdInjection,
)

__all__ = [
    "AuditOutputDiscovery",
    "AuditType",
    "BoundaryPolicy",
    "ManagedRepoAuditCapability",
    "ManagedRepoConfig",
    "RunIdInjection",
    "load_managed_repo_config",
]
