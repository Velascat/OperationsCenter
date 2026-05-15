---
campaign_id: d47e1c3a-8f92-4b15-a6d7-3c9e0f5a2b18
slug: operational-health-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - backend_health
  - queue_healing
  - recovery_policies
  - tests/unit/backend_health
  - tests/unit/queue_healing
  - tests/unit/recovery_policies
status: active
created_at: 2026-05-13T12:00:00Z
---

## Overview

Three operational health modules — `backend_health/`, `queue_healing/`, and `recovery_policies/` — ship production logic with zero unit tests. All three are pure-logic state machines with no I/O dependencies: `BackendHealthRecord` tracks health transitions and cooldowns, `QueueHealingEngine` applies deterministic rules to blocked/stale tasks, and `RecoveryBudgetTracker` enforces retry/cycle budgets with escalation. Their small size (8 files total) and absence of external dependencies make them ideal test-only campaign targets.

## Goals

1. **Add unit tests for `backend_health` models and transitions:** Cover `BackendHealthState` transition validity, `BackendFailure` recording, `BackendHealthRecord` state updates (healthy→degraded→unstable→unavailable progression, recovery path back to healthy), `HealthTransition` logging, and cooldown expiry. Test that invalid transitions are rejected and that `OPERATOR_BLOCKED` is a terminal state requiring explicit unblock. Place tests in `tests/unit/backend_health/`.

2. **Add unit tests for `queue_healing` engine and decision logic:** Cover each `QueueTransition` path in `QueueHealingEngine` — `BLOCKED_TO_BACKLOG` (task blocked beyond threshold), `BLOCKED_TO_READY_FOR_AI` (transient block resolved), `STALE_LOCK_CLEANUP` (lock held past TTL), and `ESCALATE` (retry budget exhausted). Verify time-based rules by injecting controlled timestamps. Test that healthy tasks produce no healing decision. Place tests in `tests/unit/queue_healing/`.

3. **Add unit tests for `recovery_policies` budget tracking:** Cover `RecoveryBudget` constraint enforcement (max_cycles, max_retries, max_attempts), `RecoveryBudgetDecision` outcomes (allowed vs denied with reason), and `RecoveryBudgetTracker` lineage counting. Test escalation trigger when budget is exhausted. Verify that a fresh tracker always allows the first attempt and that exceeding any single limit produces `escalate=True`. Place tests in `tests/unit/recovery_policies/`.

4. **Add cross-module integration tests for health→healing→recovery flow:** Verify the logical contract between the three modules: a `BackendHealthRecord` in `UNAVAILABLE` state should cause `QueueHealingEngine` to escalate rather than retry, and `RecoveryBudgetTracker` should confirm budget exhaustion aligns with the escalation signal. Place in `tests/unit/test_operational_health_integration.py`. Keep it to 3-5 tests covering the key handoff points.

## Constraints

- Do not modify any production source code — this is a test-only campaign.
- Each test file should be self-contained enough to run independently via `pytest tests/unit/<module>/test_<name>.py`.
- Do not introduce new third-party test dependencies. Use plain pytest fixtures and `unittest.mock` where needed.
- For time-dependent logic in `queue_healing`, inject `datetime` via fixture or `freezegun` only if it is already a project dependency; otherwise use explicit timestamp parameters.
- Test through public interfaces (`evaluate()`, `track()`, `check_budget()`, state transition methods) — do not couple to private method internals.
- The integration tests in goal 4 must not depend on I/O or external services; they exercise the logical contract between the three modules using in-memory objects only.

## Success Criteria

- `backend_health` has tests covering all 6 health states, valid/invalid transitions, cooldown logic, and operator-block semantics.
- `queue_healing` has tests covering all 4 transition types plus the no-op (healthy task) path.
- `recovery_policies` has tests for budget allow/deny/escalate across all three limits.
- Integration tests confirm the health→healing→recovery handoff produces consistent decisions.
- `pytest tests/unit/backend_health/ tests/unit/queue_healing/ tests/unit/recovery_policies/ tests/unit/test_operational_health_integration.py -v` passes with zero failures.
- No existing tests broken (full `pytest` suite stays green at 3680+ tests).
