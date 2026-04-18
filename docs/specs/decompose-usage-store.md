---
campaign_id: 71e736cd-f97f-43ff-a1b3-fe3307a78f91
slug: decompose-usage-store
phases:
  - implement
  - test
  - improve
repos:
  - ControlPlane
area_keywords:
  - execution
  - usage_store
  - budget
  - circuit_breaker
  - proposal
status: active
created_at: 2026-04-18T12:00:00Z
---

## Overview

`src/control_plane/execution/usage_store.py` is a 1,110-line God class with 44 methods that mixes budget/circuit-breaker logic, retry tracking, no-op detection, proposal satiation, validation flakiness, escalation management, cost tracking, and task artifact storage into a single `UsageStore` class. This campaign extracts cohesive method groups into focused modules with thin facade classes, keeping the `UsageStore` import path working via delegation or re-export.

## Goals

1. **Extract budget and circuit-breaker logic** — Move `budget_decision`, `remaining_exec_capacity`, `budget_decision_for_repo`, and `check_failure_rate_degradation` plus the module-level circuit-breaker constants (`_CB_THRESHOLD`, `_CB_WINDOW`, `_CB_STALENESS_HOURS`) into `execution/budget.py` as a `BudgetGuard` class. This class accepts the shared `load`/`save`/`_exclusive` primitives (or the store itself) and encapsulates all budget-window and circuit-breaker math. Update `UsageStore` to delegate these four methods to `BudgetGuard`.

2. **Extract proposal-cycle tracking** — Move `record_proposal_cycle`, `is_proposal_satiated`, `reset_satiation_window`, `record_proposal_outcome`, `proposal_success_rate`, and `record_proposal_budget_suppression` into `execution/proposal_tracking.py` as a `ProposalTracker` class. These methods form a self-contained subsystem for proposal cooldown, satiation windows, and success-rate calculation. Update `UsageStore` to delegate.

3. **Extract escalation, validation, and cost tracking** — Move `record_escalation`, `should_escalate`, `record_validation_outcome`, `is_command_flaky`, `record_execution_cost`, `get_spend_report`, `record_blocked_triage`, and `consecutive_blocks_for_task` into `execution/ops_tracking.py` as an `OpsTracker` class. These methods handle operational concerns (escalation dedup, flaky-command detection, spend reporting, blocked-task triage) that are orthogonal to the core execution budget. Update `UsageStore` to delegate.

4. **Extract disk-space and shared utilities** — Move `_check_disk_space`, `_DISK_WARN_MB`, `_DISK_MIN_MB`, `_get_lock`, `_path_locks`, `_meta_lock`, `_prune_events`, `_exec_count`, and `issue_signature` into `execution/_utils.py`. These are stateless utility functions and constants imported by multiple modules. Update all internal imports (`usage_store.py`, `autonomy_cycle/main.py`, `spec_director/main.py`, worker tests) to import from `execution._utils` instead.

## Constraints

- **Backward-compatible public API**: `from control_plane.execution.usage_store import UsageStore` and `from control_plane.execution import UsageStore` must continue to work. `UsageStore` retains all 44 method signatures via thin delegation (e.g., `self._budget.budget_decision(now=now)`). No caller changes required.
- **`_check_disk_space` re-export**: `usage_store.py` must re-export `_check_disk_space` from `_utils` so that the 4 existing `from control_plane.execution.usage_store import _check_disk_space` call sites keep working without modification.
- **No logic changes**: Each goal is a pure extract-and-delegate refactor. Do not rename methods, change signatures, or alter behavior.
- **Incremental**: Each goal is a standalone commit. Tests must pass after each extraction.
- **Goal ordering**: Goal 4 (utilities) should be done first since the other three modules will import from `_utils`. Goals 1–3 are independent of each other after Goal 4.
- **Shared JSON storage unchanged**: All extracted classes continue to read/write the same `usage.json` file via `UsageStore.load`/`save`. No schema changes.
- **Test files stay as-is**: `test_execution_controls.py` and related tests should not be split or modified beyond import adjustments if needed.

## Success Criteria

- `usage_store.py` is under 400 lines, containing `UsageStore` with `load`, `save`, `_exclusive`, core execution-recording methods (`record_execution`, `record_execution_outcome`, `record_quality_warning`, `record_scope_violation`, `record_kodo_quota_event`, `record_execution_duration`, `median_execution_duration`, `audit_export`, `record_skip`, `record_retry_cap`, `record_task_artifact`, `get_task_artifact`), retry/noop methods, and delegation stubs.
- Four new modules exist: `_utils.py`, `budget.py`, `proposal_tracking.py`, `ops_tracking.py`.
- `python -m pytest tests/test_execution_controls.py tests/test_s7.py tests/test_proposer.py tests/test_decision.py tests/test_worker_entrypoint.py tests/test_repo_aware_autonomy_chain.py` passes with zero import errors.
- `ruff check src/control_plane/execution/` reports no new lint violations.
- Every previously-importable symbol from `execution.usage_store` is still importable from that path.