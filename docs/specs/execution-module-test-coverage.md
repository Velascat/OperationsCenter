---
campaign_id: d4e7a1c3-8f29-4b6e-a1d5-3c9e2f7b4a08
slug: execution-module-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - operations-center
area_keywords:
  - execution
  - usage_store
  - campaign_store
  - workspace
status: active
created_at: 2026-04-28T00:00:00Z
---

## Overview

The `src/operations_center/execution/` module contains three substantial production modules — `usage_store.py` (~1100 lines, 30+ public methods), `campaign_store.py` (~200 lines), and `workspace.py` (~170 lines) — with minimal or zero dedicated unit test coverage. Existing tests in `test_execution_controls.py` cover only 5 basic budget/retry/noop scenarios on `UsageStore`, leaving the circuit breaker, escalation tracking, spend reporting, proposal satiation, flaky-command detection, duration baselines, and audit export entirely untested. `CampaignStore` and `WorkspaceManager` have no dedicated tests at all.

## Goals

1. **Add unit tests for `CampaignStore` CRUD and status transitions** — Cover `create` (including idempotent re-create), `record_step_done`, `record_step_cancelled`, `get`, `list_campaigns` with status filter, and verify computed status derivation (`in_progress` → `partial` → `completed` / `cancelled`). Place tests in `tests/unit/execution/test_campaign_store.py`.

2. **Add unit tests for `UsageStore` circuit breaker, escalation, and spend reporting** — Cover `budget_decision` circuit-breaker path (≥80% failure rate opens breaker, version-transition window bypass, staleness auto-recovery), `should_escalate` with cooldown gating, `consecutive_blocks_for_task`, `get_spend_report` per-repo aggregation, and `audit_export` event flattening. Place tests in `tests/unit/execution/test_usage_store_advanced.py`.

3. **Add unit tests for `UsageStore` proposal satiation and validation tracking** — Cover `is_proposal_satiated` (below window threshold, dedup-ratio gating, `reset_satiation_window`), `proposal_success_rate` (neutral default, normal rate), `is_command_flaky` (below-window safety, threshold crossing), `check_failure_rate_degradation`, and `median_execution_duration`. Place tests in `tests/unit/execution/test_usage_store_proposals.py`.

4. **Add unit tests for `WorkspaceManager` prepare and finalize paths** — Mock `GitClient` and `subprocess.run` to test `prepare` (clone into empty dir, fail on non-empty), `finalize` (commit + push + PR creation, no-op when no new commits, graceful failure on push error). Place tests in `tests/unit/execution/test_workspace_manager.py`.

## Constraints

- All new test files go under `tests/unit/execution/`. Create `__init__.py` if missing.
- Use `tmp_path` and `monkeypatch` fixtures — no real git clones, no network calls.
- Mock external boundaries (`subprocess.run`, `GitClient`, `GitHubPRClient`, `shutil.disk_usage`) rather than patching internals.
- Follow the existing test style: standalone functions (not classes), `pytest` assertions, descriptive `test_<behavior>` names.
- Do not modify production code. This campaign is test-only.
- Each goal should produce a single test file with 5-12 test functions.

## Success Criteria

- `pytest tests/unit/execution/ -v` passes with zero failures.
- Every public method on `CampaignStore` is exercised by at least one test.
- The `UsageStore` circuit breaker, escalation, spend, satiation, flaky-command, degradation, and duration-baseline code paths each have at least one positive and one negative test.
- `WorkspaceManager.prepare` and `WorkspaceManager.finalize` each have at least three tests covering success, no-op, and error paths.
- `ruff check tests/unit/execution/` reports no lint violations.