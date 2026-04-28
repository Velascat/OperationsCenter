---
campaign_id: 7a3e2f1c-9b84-4d5e-a6c1-0e8f3d72b519
slug: execution-workspace-manager-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - operations-center
area_keywords:
  - execution
  - adapters/git
  - adapters/github_pr
status: active
created_at: 2026-04-28T00:00:00Z
---

## Overview

The `execution/workspace.py` WorkspaceManager was added in commit d61144f but has zero dedicated test coverage. This class handles the critical git-clone → commit → push → PR-creation lifecycle that connects execution results to visible artifacts (branches and pull requests). Without tests, regressions in workspace preparation or finalization silently break the entire execution pipeline.

## Goals

1. **Add unit tests for `WorkspaceManager.prepare()`** — Cover the happy path (clone into empty dir, identity set, base checkout, task branch creation), the guard against non-empty workspace dirs, and clone failure propagation. Mock `subprocess.run` and `GitClient` to avoid real git operations.

2. **Add unit tests for `WorkspaceManager.finalize()`** — Cover: (a) no-op when `result.success` is False, (b) no-op when workspace is not a git repo, (c) commit+push when there are changed files and new commits, (d) skip push when no new commits exist vs base, (e) push failure returns original result with `branch_pushed=False`, (f) PR creation gated on `_await_review` set membership and token presence.

3. **Add unit tests for coordinator–workspace integration paths** — Extend `tests/unit/execution/test_coordinator.py` with cases where `workspace_manager` is provided: workspace prep failure returns `FAILED` result with `BACKEND_ERROR` category, and successful finalize propagates `branch_pushed`/`pull_request_url` onto the outcome.

4. **Add edge-case tests for `_commit_message` and `_has_new_commits` helpers** — Cover multiline goal text truncation, empty/None goal text fallback to run_id, and `_has_new_commits` when `git rev-list` fails or returns non-integer output.

## Constraints

- All new tests go in `tests/unit/execution/test_workspace.py` (goals 1, 2, 4) and extend `tests/unit/execution/test_coordinator.py` (goal 3).
- Mock `subprocess.run`, `GitClient`, and `GitHubPRClient` — no real git repos, no network calls.
- Follow existing test patterns: stub classes with `_Recording`/`_Stub` prefix, `pytest` style, no test base classes.
- Do not modify production code unless a bug is discovered during testing.
- Keep each test focused on a single behavior; prefer many small tests over few large ones.

## Success Criteria

- `pytest tests/unit/execution/test_workspace.py -v` passes with ≥15 tests covering all documented behaviors.
- `pytest tests/unit/execution/test_coordinator.py -v` passes with ≥2 new workspace-integration tests.
- Every public method and helper on `WorkspaceManager` has at least one happy-path and one error-path test.
- `ruff check` and `ty check` report no new violations in test files.