"""
contracts — canonical cross-repo types for the AI coding platform.

These models are the single source of truth for how platform components
(OperationsCenter, SwitchBoard, backend adapters) exchange structured data.
They are Pydantic v2, fully serialisable, and backend-agnostic.

Canonical ownership:  OperationsCenter (src/operations_center/contracts/)
Consumers:            SwitchBoard, kodo adapters, Archon, any backend
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
