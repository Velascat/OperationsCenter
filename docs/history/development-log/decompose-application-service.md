---
campaign_id: 7a3e1d2f-8b4c-4e6a-9f01-d3c5a7b2e8f4
slug: decompose-application-service
phases:
  - implement
  - test
  - improve
repos:
  - operations-center
area_keywords:
  - application
  - service
  - execution
status: active
created_at: 2026-04-18T19:10:00Z
---

## Overview

The `src/operations_center/application/service.py` file is 1613 lines with a single `ExecutionService` class containing 28 methods that span task execution, self-review orchestration, PR-fix workflows, validation baseline checks, reporting/comment generation, and git branch operations. This campaign decomposes it into focused modules while keeping the public `ExecutionService` facade intact for backward compatibility.

## Goals

1. **Extract reporting helpers into `application/reporting.py`**: Move `_comment_markdown`, `_validation_excerpt`, `_stderr_excerpt`, `_diff_stat_excerpt`, `_count_quality_suppressions`, and `_log_event` (lines ~971–1091) into a standalone module. These are pure/static methods with no dependency on `ExecutionService` state. Update `service.py` to import and delegate.

2. **Extract PR-fix and review logic into `application/pr_workflow.py`**: Move `run_fix_pr_task`, `_find_open_fix_validation_task`, `_maybe_create_fix_validation_task`, `_build_fix_validation_description`, `_write_pr_review_state`, `create_revert_branch`, `rebase_branch`, `run_review_pass`, and `run_self_review_pass` (lines ~923–end) into a new module. These methods form a cohesive PR lifecycle sub-system. The new class receives only the dependencies it needs (settings, git client, kodo adapter).

3. **Extract baseline validation into `application/baseline.py`**: Move `_BaselineResult`, `_run_baseline_validation`, `_validate_task_contract`, and `_build_scope_constraints_section` (lines ~29–66, 79–98, 1099–1296) into a dedicated module. These handle pre-execution contract checking and baseline validation and have minimal coupling to the core execution flow.

4. **Ensure all existing tests pass without modification**: The existing 17 test files that import from `application.service` must continue to work. Add re-exports in `service.py` for any symbols that moved (e.g., `TaskContractError`, `_SelfReviewVerdict`) so downstream imports remain valid.

## Constraints

- **Preserve the `ExecutionService` class as the public API**: Callers must not need to change imports. The class can delegate to extracted modules internally.
- **No new external dependencies**: Only reorganize existing code; do not introduce new packages.
- **Keep `_SelfReviewVerdict` importable from `application.service`**: Tests reference it directly; add a re-export if it moves.
- **Follow existing patterns**: The `decision/` package already demonstrates this decomposition style (service.py + policy.py + candidate_builder.py + models.py). Mirror that structure.
- **One module per goal**: Each extracted file should be independently testable.

## Success Criteria

- `service.py` is reduced to ≤500 lines (from 1613), containing only `ExecutionService.__init__`, `run_task`, and thin delegation methods.
- `ruff check` and `ty check` pass with zero new errors.
- `pytest tests/` passes with no failures and no test file modifications.
- Each new module (`reporting.py`, `pr_workflow.py`, `baseline.py`) has a clear single responsibility and no circular imports back to `service.py`.