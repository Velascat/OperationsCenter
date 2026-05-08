---
campaign_id: a5a73307-3ffd-4659-ba1d-09dc77019f98
slug: decision-rules-test-coverage
phases:
  - implement
  - test
  - improve
repos:
  - OperationsCenter
area_keywords:
  - decision
  - decision/rules
  - tests/unit/decision
status: active
created_at: 2026-05-08T18:00:00Z
---

## Overview

The `decision/` module contains 14 rule evaluators that score and filter candidate tasks from derived insights, yet only 3 have direct unit tests. Each rule's `evaluate()` method accepts a `Sequence[DerivedInsight]` — a pure-data input with no I/O — making them highly testable in isolation. This campaign adds focused unit tests for the 11 untested rules and deepens coverage of the service-layer suppression logic.

## Goals

1. **Add unit tests for 6 untested decision rules (batch 1):** `arch_promotion`, `backlog_promotion`, `ci_pattern`, `coverage_gap`, `hotspot_concentration`, `lint_cluster`. Each test file must cover the positive-match path (rule fires a candidate with correct confidence/priority) and the negative path (non-matching insights produce no candidate). Place tests in `tests/unit/decision/rules/`.

2. **Add unit tests for 5 remaining untested decision rules (batch 2):** `lint_fix`, `todo_accumulation`, `type_improvement`, `validation_pattern`, and any other rule file not covered by batch 1. Same positive/negative pattern. Verify evidence synthesis (the rule attaches the right insight references to its candidate).

3. **Add targeted tests for DecisionEngineService suppression logic:** Cover `_count_proposals_last_24h()`, `_stale_open_dedup_keys()`, and the velocity-cap / budget-gate branches in `service.py`. Use the existing `_make_insight_artifact()` helper pattern from current decision tests.

4. **Add tests for ChainPolicy cooldown and sequencing:** Cover `chain_policy.py` — verify that cooldown windows suppress re-proposal and that sequencing constraints order candidates correctly.

## Constraints

- Reuse the existing `_make_insight()` and `_make_insight_artifact()` fixture helpers already present in `tests/unit/decision/`. Extract them to a shared `conftest.py` if they are needed across multiple test files.
- Do not modify any production source code — this is a test-only campaign.
- Each test file should be self-contained enough to run independently via `pytest tests/unit/decision/rules/test_<rule>.py`.
- Do not introduce new third-party test dependencies (no hypothesis, no factory_boy). Use plain pytest fixtures and `unittest.mock` where needed.
- Avoid coupling tests to internal rule implementation details (private methods). Test through the public `evaluate()` / `apply()` interface.

## Success Criteria

- All 14 decision rules have at least one positive-match and one negative-match test.
- `DecisionEngineService` suppression methods have dedicated unit tests covering budget exhaustion, velocity cap, and staleness dedup.
- `ChainPolicy` has tests for cooldown suppression and sequencing order.
- `pytest tests/unit/decision/ -v` passes with zero failures.
- No existing tests broken (full `pytest` suite stays green at 2733+ tests).
