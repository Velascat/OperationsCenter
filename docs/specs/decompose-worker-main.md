---
campaign_id: dbcc4c86-e417-44e0-bc48-e05850745b2e
slug: decompose-worker-main
phases:
  - implement
  - test
  - improve
repos:
  - ControlPlane
area_keywords:
  - entrypoints/worker
  - worker
  - proposal
  - reconcile
  - scan
status: active
created_at: 2026-04-17T00:00:00Z
---

## Overview

`src/control_plane/entrypoints/worker/main.py` is an 8 052-line monolith containing 164 top-level functions that mix proposal logic, AST-based code scanning, board reconciliation, blocked-issue triage, task execution handlers, and the main watch loop. This campaign extracts cohesive function groups into focused submodules inside the `entrypoints/worker/` package, turning the single file into a package of ≤ 800-line modules with a thin `main.py` re-exporting the public API.

## Goals

1. **Extract proposal subsystem** — Move all proposal-related functions (`build_proposal_candidates`, `proposal_specs_from_findings`, `handle_propose_cycle`, `create_proposed_task_if_missing`, `proposal_cooldown_active`, `recently_proposed`, `record_proposed`, `_score_proposal_utility`, `_normalise_proposal_title`, `idle_board_*`, `ProposalSpec`, `ProposalCycleResult`, and proposal memory helpers) into `entrypoints/worker/proposals.py`. Update imports in `main.py` to re-export from the new module.

2. **Extract code-scanning / static-analysis functions** — Move AST-based scanning functions (`_safety_findings`, `_dead_code_findings`, `_type_coverage_findings`, `_recursion_findings`, `_ast_complexity_findings`, `_ruff_findings`, `_all_todo_findings`, `_scan_file_for_signals`, `_find_untested_module`, `discover_improvement_candidates`, `_inspect_repo`, `_py_source_files`, `_run_tool`) into `entrypoints/worker/scanning.py`.

3. **Extract reconciliation and board-maintenance functions** — Move `reconcile_stale_blocked_issues`, `reconcile_campaign_trackers`, `reconcile_stale_running_issues`, `cleanup_orphaned_workspaces`, `cleanup_stale_global_editables`, `detect_post_merge_regressions`, and the `handle_*_scan` family (`handle_feedback_loop_scan`, `handle_workspace_health_check`, `handle_awaiting_input_scan`, `handle_priority_rescore_scan`, `handle_stale_autonomy_task_scan`, `handle_dependency_update_scan`) into `entrypoints/worker/reconciliation.py`.

4. **Extract task-handler dispatch functions** — Move `handle_goal_task`, `handle_test_task`, `handle_improve_task`, `handle_fix_pr_task`, `handle_blocked_triage`, and their direct helpers (`classify_blocked_issue`, `build_improve_triage_result`, `classify_execution_result`, `goal_failure_needs_manual_env_fix`, `_is_quota_exhausted_result`, `_check_cross_repo_impact`) into `entrypoints/worker/task_handlers.py`.

## Constraints

- **Backward-compatible imports**: `main.py` must re-export every moved symbol so that `from control_plane.entrypoints.worker.main import X` continues to work for external callers and tests. Use `from .proposals import *` style re-exports.
- **No logic changes**: Each goal is a pure move-and-import refactor. Do not rename functions, change signatures, or alter behavior.
- **Incremental**: Each goal is a standalone PR-able commit. Tests must pass after each extraction.
- **Convert to package first**: The first commit of Goal 1 should convert `worker/` from a single `main.py` to a Python package (`__init__.py` + `main.py`), if not already done.
- **Shared helpers stay in `main.py` until the final goal** — small utility functions used across multiple new modules (e.g., `issue_label_names`, `parse_context_value`) remain in `main.py` to avoid circular imports. They can be collected into a `_helpers.py` in the improve phase.
- **Test file stays as-is**: `test_worker_entrypoint.py` (4 285 lines) should not be split in this campaign; it will be addressed separately.

## Success Criteria

- `worker/main.py` is under 2 500 lines (down from 8 052).
- Four new modules exist: `proposals.py`, `scanning.py`, `reconciliation.py`, `task_handlers.py`.
- `python -m pytest tests/test_worker_entrypoint.py` passes with zero import errors and no test changes.
- `ruff check src/control_plane/entrypoints/worker/` reports no new lint violations.
- Every previously-importable symbol from `worker.main` is still importable from that path.