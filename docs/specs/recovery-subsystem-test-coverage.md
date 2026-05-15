---
campaign_id: f7e3a1c4-8b92-4d5f-a6e0-3c9f1d2e7b84
slug: recovery-subsystem-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - queue_healing
  - recovery
  - recovery_policies
  - tests/unit
status: active
created_at: 2026-05-13T23:59:00Z
---

## Overview

The three recovery subsystem modules — `queue_healing/` (146 LOC), `recovery/` (161 LOC), and `recovery_policies/` (68 LOC) — contain pure deterministic logic for self-healing queue transitions, parked-state lifecycle, and recovery budget accounting. Despite being critical to the oversight loop's autonomous operation, they share only 9 tests in a single top-level file (`test_self_healing_recovery.py`) with no dedicated test directories, leaving multiple decision branches and edge cases unexercised.

## Goals

1. **Add dedicated `QueueHealingEngine` unit tests (`tests/unit/queue_healing/`):** Cover all 6 decision paths in `engine.py::decide()` — non-blocked passthrough, recovery-attempt budget exhaustion, retry-count budget exhaustion, duplicate-suppression deadlock unblock, stale-blocked demotion, and the catch-all no-match path. Include edge cases: `updated_at=None` staleness immunity, custom threshold overrides, case-insensitive state matching.

2. **Add dedicated `recovery/` unit tests (`tests/unit/recovery/`):** Cover `should_unpark()` for all three outcomes (evidence hash change, triggered condition match, stay parked). Cover `ParkedStateStore` round-trip persistence (save → load), missing-file returns None, and `clear()` idempotency. Cover `RecoveryTelemetryEvent` and `WatcherRecoveryTelemetry` dataclass construction with representative field combinations.

3. **Add dedicated `RecoveryBudgetTracker` unit tests (`tests/unit/recovery_policies/`):** Cover `record_cycle()` with evidence-changed reset vs. monotonic increment past threshold. Cover `record_retry()` with equivalent vs. non-equivalent retry sequences and escalation trigger. Cover `record_recovery_attempt()` budget exhaustion. Verify all three methods return correct `RecoveryBudgetDecision.escalate` values at boundary conditions.

4. **Refactor `test_self_healing_recovery.py` into the new directories:** Move existing tests that overlap with the new coverage into the appropriate `tests/unit/{queue_healing,recovery,recovery_policies}/` directories. Leave `test_self_healing_recovery.py` only if it contains integration-level tests that span multiple modules; otherwise delete it.

## Constraints

- Test-only campaign — do not modify any production source code.
- Each test file must be independently runnable via `pytest tests/unit/<module>/test_<name>.py`.
- No new third-party test dependencies. Use plain pytest fixtures and `unittest.mock` where needed.
- Test through public interfaces (`decide()`, `should_unpark()`, `record_cycle()`, etc.) — do not test private methods directly.
- Preserve all existing tests in `test_self_healing_recovery.py` that test cross-module integration behavior (e.g., tests that wire `QueueHealingEngine` output into `RecoveryBudgetTracker`); only move tests that are purely unit-scoped.

## Success Criteria

- `QueueHealingEngine.decide()` has at least one test per decision branch (6 branches, minimum 6 tests).
- `should_unpark()` has tests for all three outcome paths.
- `ParkedStateStore` has round-trip, missing-file, and clear tests.
- `RecoveryBudgetTracker` has boundary-condition tests for all three `record_*` methods.
- `pytest tests/unit/queue_healing/ tests/unit/recovery/ tests/unit/recovery_policies/ -v` passes with zero failures.
- Full suite stays green at 2733+ tests.
- No test in the new directories imports or depends on I/O beyond `tmp_path`.