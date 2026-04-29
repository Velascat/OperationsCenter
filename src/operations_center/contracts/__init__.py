"""
contracts — OperationsCenter's internal subtype of the CxRP envelope.

The canonical cross-repo wire contract is **CxRP**
(``cxrp.contracts``). The classes here are OperationsCenter's *internal*
Pydantic representation: they layer narrower types (``LaneName``,
``BackendName``, structured ``TaskTarget``/``BranchPolicy``/
``ValidationProfile``) on top of CxRP's open envelope so adapters and
policy can rely on stricter shapes within OC.

At repo boundaries (HTTP between OC ↔ SwitchBoard, JSON written for
OperatorConsole, run artifacts) these models are translated to CxRP shape
via ``operations_center.contracts.cxrp_mapper``. The wire format is CxRP;
this module is the OC-internal subtype.

Canonical wire format:  CxRP v0.2 (https://github.com/Velascat/CxRP)
Internal owner:         OperationsCenter (Pydantic; this package)
"""

from .enums import (
    ArtifactType,
    BackendName,
    ExecutionMode,
    ExecutionStatus,
    FailureReasonCategory,
    LaneName,
    Priority,
    RiskLevel,
    TaskType,
    ValidationStatus,
)
from .common import (
    BranchPolicy,
    ChangedFileRef,
    ExecutionConstraints,
    TaskTarget,
    ValidationProfile,
    ValidationSummary,
)
from .proposal import TaskProposal
from .routing import LaneDecision
from .execution import ExecutionArtifact, ExecutionRequest, ExecutionResult, RunTelemetry

__all__ = [
    # enums
    "ArtifactType",
    "BackendName",
    "ExecutionMode",
    "ExecutionStatus",
    "FailureReasonCategory",
    "LaneName",
    "Priority",
    "RiskLevel",
    "TaskType",
    "ValidationStatus",
    # value objects
    "BranchPolicy",
    "ChangedFileRef",
    "ExecutionConstraints",
    "TaskTarget",
    "ValidationProfile",
    "ValidationSummary",
    # top-level models
    "TaskProposal",
    "LaneDecision",
    "ExecutionArtifact",
    "ExecutionRequest",
    "ExecutionResult",
    "RunTelemetry",
]
