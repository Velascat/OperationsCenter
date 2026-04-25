---
campaign_id: 5a2d0cf0-16e2-4437-b170-d0d990e27f27
slug: decompose-execution-service
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - application/service
  - execution_service
  - review_pass
  - fix_validation
status: active
created_at: 2026-04-18T18:00:00Z
---

## Overview

`src/operations_center/application/service.py` is a 1,613-line file whose `ExecutionService` class holds 27 methods spanning five distinct responsibilities: the core `run_task` orchestration (750 lines), output formatting helpers, baseline-validation / fix-task creation, git operations (revert, rebase), and review-pass execution (self-review + human-review + fix-PR). This campaign extracts the non-orchestration method groups into focused modules inside `application/`, leaving `service.py` with only `ExecutionService.__init__`, `run_task`, contract validation, and delegation stubs.

## Goals

1. **Extract output-formatting helpers into `application/formatting.py`** — Move the static/class methods `_comment_markdown`, `_validation_excerpt`, `_stderr_excerpt`, `_diff_stat_excerpt`, `_count_quality_suppressions`, `_is_internal_execution_path`, and `_meaningful_changed_files` into a new `application/formatting.py` module as free functions (they are all `@staticmethod` or `@classmethod` with no `self` state). Also move the module-level helper `_build_scope_constraints_section`. Update `ExecutionService` to import and delegate. These ~180 lines of pure formatting logic have no dependency on service state.

2. **Extract baseline-validation and fix-task logic into `application/baseline.py`** — Move `_run_baseline_validation`, `_maybe_create_fix_validation_task`, `_build_fix_validation_description`, `_find_open_fix_validation_task`, `_check_repeated_unknown_failures`, and `_assert_goal_sections_unique` plus the helper classes `_BaselineResult` and `TaskContractError` into `application/baseline.py`. These methods handle pre-execution health checks and fix-task lifecycle, forming a cohesive group (~350 lines). `ExecutionService` delegates to a `BaselineGuard` class or to free functions imported from the new module.

3. **Extract review-pass and git-operation methods into `application/review_ops.py`** — Move `run_review_pass`, `run_self_review_pass`, `run_fix_pr_task`, `create_revert_branch`, `rebase_branch`, `_write_pr_review_state`, and the `_SelfReviewVerdict` dataclass into `application/review_ops.py` as a `ReviewOperations` class (or mixin) that receives the same adapter dependencies via its constructor. These ~350 lines handle post-execution review workflows that are independent of the core `run_task` orchestration. `ExecutionService` composes or delegates to the new class.

## Constraints

- **Backward-compatible imports**: `from operations_center.application.service import ExecutionService`, `_SelfReviewVerdict`, `_BaselineResult`, `TaskContractError`, and `_build_scope_constraints_section` must continue to work. `service.py` re-exports every moved symbol.
- **No logic changes**: Each goal is a pure extract-and-delegate refactor. No renaming, no signature changes, no behavioral modifications.
- **Incremental**: Each goal is a standalone PR-able commit. Tests must pass after each extraction.
- **Goal ordering**: Goal 1 is independent. Goal 2 is independent. Goal 3 depends on Goal 1 (review-pass methods call formatting helpers). Execute Goal 1 first, then Goals 2 and 3 in either order.
- **`run_task` stays in `service.py`**: The 750-line `run_task` method is the core orchestrator and should remain in `service.py`. Splitting it further requires behavioral refactoring (e.g., introducing a pipeline pattern), which belongs in a future campaign.
- **Test files stay as-is**: `test_self_review_verdict.py`, `test_circuit_breaker.py`, `test_fix_validation_task.py`, `test_scope_injection.py`, `test_goal_section_guard.py`, and `test_service_reporting.py` should not be modified beyond import adjustments if any are needed (re-exports should prevent this).
- **`application/__init__.py` unchanged**: The lazy `__getattr__` in `__init__.py` already proxies `ExecutionService`; no changes needed there.

## Success Criteria

- `service.py` is under 900 lines, containing `ExecutionService` with `__init__`, `run_task`, `_repo_target_for`, `task_branch`, `_validate_task_contract`, `_log_event`, and thin delegation stubs.
- Three new modules exist: `formatting.py` (~180 lines), `baseline.py` (~350 lines), `review_ops.py` (~350 lines).
- All existing test files pass without modification: `pytest tests/test_self_review_verdict.py tests/test_circuit_breaker.py tests/test_circuit_breaker_e2e.py tests/test_fix_validation_task.py tests/test_scope_injection.py tests/test_goal_section_guard.py tests/test_service_reporting.py tests/test_policy_retry.py tests/test_worker_logging.py tests/test_service_bootstrap.py tests/test_execution_modes.py tests/test_validation_retry_integration.py`.
- `ruff check src/operations_center/application/` reports no new lint violations.
- Every previously-importable symbol from `application.service` is still importable from that path.
