# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""
enums.py — canonical enumerated types for the platform contract layer.

All enums are str-based so they round-trip cleanly through JSON/YAML
without custom serialisers.
"""

from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    """Broad category of work being proposed."""
    LINT_FIX = "lint_fix"
    BUG_FIX = "bug_fix"
    SIMPLE_EDIT = "simple_edit"
    TEST_WRITE = "test_write"
    DOCUMENTATION = "documentation"
    REFACTOR = "refactor"
    FEATURE = "feature"
    DEPENDENCY_UPDATE = "dependency_update"
    UNKNOWN = "unknown"


class LaneName(str, Enum):
    """Execution lanes available in the platform."""
    CLAUDE_CLI = "claude_cli"
    CODEX_CLI = "codex_cli"
    AIDER_LOCAL = "aider_local"


class BackendName(str, Enum):
    """Backend implementations that execute tasks inside a lane."""
    DIRECT_LOCAL = "direct_local"
    AIDER_LOCAL = "aider_local"
    KODO = "kodo"
    ARCHON = "archon"
    ARCHON_THEN_KODO = "archon_then_kodo"
    OPENCLAW = "openclaw"
    DEMO_STUB = "demo_stub"


class ExecutionMode(str, Enum):
    """High-level execution strategy for the run."""
    GOAL = "goal"
    FIX_PR = "fix_pr"
    TEST_CAMPAIGN = "test_campaign"
    IMPROVE_CAMPAIGN = "improve_campaign"


class ExecutionStatus(str, Enum):
    """Terminal or in-progress outcome of an execution run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ArtifactType(str, Enum):
    """Type of artifact produced during execution."""
    DIFF = "diff"
    PATCH = "patch"
    VALIDATION_REPORT = "validation_report"
    LOG_EXCERPT = "log_excerpt"
    GOAL_FILE = "goal_file"
    PR_URL = "pr_url"
    BRANCH_REF = "branch_ref"


class ValidationStatus(str, Enum):
    """Outcome of a validation step."""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class RiskLevel(str, Enum):
    """Risk estimate for a proposed change."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Priority(str, Enum):
    """Scheduling priority for the task."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class FailureReasonCategory(str, Enum):
    """Coarse failure category for routing and retry decisions."""
    VALIDATION_FAILED = "validation_failed"
    BACKEND_ERROR = "backend_error"
    UNSUPPORTED_REQUEST = "unsupported_request"
    TIMEOUT = "timeout"
    NO_CHANGES = "no_changes"
    CONFLICT = "conflict"
    POLICY_BLOCKED = "policy_blocked"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ROUTING_ERROR = "routing_error"
    UNKNOWN = "unknown"
