---
campaign_id: 3ddf1c79-ffd0-4880-a7f7-6ef6d198dbf4
slug: watcher-entrypoint-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - entrypoints/board_worker
  - entrypoints/pr_review_watcher
  - adapters
status: cancelled
created_at: 2026-04-27T00:00:00Z
---

## Overview

The `board_worker` and `pr_review_watcher` entrypoints are the two newest and most complex watcher processes in the system, yet they have zero test coverage (0 test files, 0 references in the test suite). Both contain intricate state-machine logic, subprocess orchestration, and integrations with Plane and GitHub APIs that are prime targets for regression. This campaign adds focused unit tests that exercise their core logic paths using mocked external dependencies.

## Goals

1. **Add unit tests for `board_worker` claim-and-dispatch logic** — Test `_claim_next` (filtering by role kinds, repo membership, oldest-first ordering, transition to Running), `_extract_goal` (section extraction and title fallback), `_task_type_from_kind` mapping, `_label_value` / `_has_label` helpers, and the outcome handlers (`_handle_success`, `_handle_failure`, `_create_follow_up`) with a mocked Plane client. Target: `tests/unit/entrypoints/test_board_worker.py`.

2. **Add unit tests for `pr_review_watcher` state machine** — Test state file lifecycle (`_new_state`, `_load_state`, `_save_state` round-trip via `tmp_path`), Phase 1 flow (`_phase1` with LGTM → merge, CONCERNS → loop, escalation to Phase 2 at max loops), and Phase 2 flow (`_phase2` with `/lgtm` comment, 👍 reaction approval, timeout auto-merge, revision loop, max-loops auto-merge). Mock `GitHubPRClient`, `PlaneClient`, and `_run_pipeline`. Target: `tests/unit/entrypoints/test_pr_review_watcher.py`.

3. **Extract shared `_label_value` helper to avoid duplication** — Both `board_worker/main.py` and `pr_review_watcher/main.py` define identical `_label_value` functions. Extract to a shared location (e.g., `operations_center.adapters.plane` or a new `operations_center.utils.labels` module), update both entrypoints to import it, and add a dedicated test for edge cases (missing prefix, mixed case, multiple matches).

4. **Add integration-style test for `board_worker` `_process_issue` pipeline** — Using monkeypatched `subprocess.run` to return canned JSON for the planning and execution stages, verify the full `_process_issue` flow from claimed issue through to Plane state transitions for both success and failure paths. This validates the subprocess wiring without requiring real processes.

## Constraints

- All new tests must use `pytest` with `tmp_path` for any filesystem state; no real GitHub or Plane API calls.
- Mock boundaries: `GitHubPRClient`, `PlaneClient`, and `subprocess.run` are the mock seams. Internal pure functions should be tested directly.
- Do not refactor the entrypoint control flow or phase logic — only extract the duplicated `_label_value` helper and add tests around existing behavior.
- New test files go under `tests/unit/entrypoints/` following the existing `tests/unit/` package structure (add `__init__.py` as needed).
- Keep each test file focused; avoid fixtures that couple the two entrypoints.

## Success Criteria

- `pytest tests/unit/entrypoints/test_board_worker.py` passes with ≥ 15 test cases covering claim logic, outcome handlers, and helpers.
- `pytest tests/unit/entrypoints/test_pr_review_watcher.py` passes with ≥ 15 test cases covering both phases of the state machine and state file persistence.
- The duplicated `_label_value` function exists in exactly one shared module, both entrypoints import it, and `ruff check` reports no issues.
- `pytest tests/ -q` continues to collect and pass all existing 2791+ tests with no regressions.