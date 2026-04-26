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
