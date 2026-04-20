---
campaign_id: c7e4a91b-3f28-4d0e-b516-8a2d6e9f04c3
slug: test-untested-insight-derivers
phases:
  - implement
  - test
  - improve
repos:
  - control-plane
area_keywords:
  - insights/derivers
  - ci_pattern
  - validation_pattern
  - proposal_outcome
  - noop_loop
status: active
created_at: 2026-04-20T12:00:00Z
---

## Overview

Four insight derivers — `CIPatternDeriver`, `ValidationPatternDeriver`, `ProposalOutcomeDeriver`, and `NoOpLoopDeriver` — have zero dedicated test coverage. Together they represent ~400 lines of production logic that reads observer snapshots and filesystem state to emit derived insights. This campaign adds focused unit tests for each deriver covering happy paths, edge cases, and boundary conditions.

## Goals

1. **Add `tests/test_ci_pattern_deriver.py` for `insights/derivers/ci_pattern.py`**: Test `derive()` returns empty on empty snapshots, returns empty when `ci_history.status == "unavailable"`, emits a `ci_pattern/failing` insight when `failing_checks` is non-empty, emits a `ci_pattern/flaky` insight when `flaky_checks` is non-empty, and emits both insights simultaneously when both fields are populated. Verify evidence dictionaries contain the expected keys (`failing_checks`, `flaky_checks`, `failure_rate`, `runs_checked`, `source`). Target: ≥5 test cases.

2. **Add `tests/test_validation_pattern_deriver.py` for `insights/derivers/validation_pattern.py`**: Test `derive()` returns empty on empty snapshots, returns empty when `validation_history.status == "unavailable"`, returns empty when status is `"patterns_detected"` but `tasks_with_repeated_failures` is empty, emits a `validation_pattern/repeated_failures` insight with correct evidence when failures exist, and verify it caps `top_task_ids` at 3 entries when more than 3 failing tasks are present. Target: ≥5 test cases.

3. **Add `tests/test_proposal_outcome_deriver.py` for `insights/derivers/proposal_outcome.py`**: Use `tmp_path` to create mock `state/proposal_feedback/*.json` files and `tools/report/control_plane/proposer/*/proposal_results.json` artifacts. Test: returns empty when no feedback files exist, returns empty when feedback exists but no proposer artifacts match, returns empty when fewer than `_MIN_RECORDS_FOR_INSIGHT` (5) records exist for a family, emits `high_escalation_rate` when escalation rate ≥ 0.4, and does not fire when escalation rate is below threshold. Patch `_FEEDBACK_DIR` and `_PROPOSER_ROOT` module constants to point at `tmp_path` subdirectories. Target: ≥6 test cases.

4. **Add `tests/test_noop_loop_deriver.py` for `insights/derivers/noop_loop.py`**: Use the constructor's `proposer_root` and `feedback_root` kwargs to point at `tmp_path` subdirectories. Test: returns empty on empty snapshots, returns empty when no proposer artifacts exist, does not flag a family with fewer than `min_proposals` (3) proposals, flags a family with ≥3 proposals and 0 merges, does not flag a family that has at least one merge, respects the look-back window by ignoring proposals older than `look_back_days`, and correctly handles multiple families independently. Target: ≥7 test cases.

## Constraints

- **No production code changes**: This campaign is test-only. Do not modify any files under `src/`.
- **No network or live service dependencies**: All filesystem access must use `tmp_path`. For `CIPatternDeriver` and `ValidationPatternDeriver`, construct `RepoStateSnapshot` objects in-memory using the existing model factories from `control_plane.observer.models`.
- **Follow existing test patterns**: Mirror the fixture style in `tests/test_phase5_derivers.py` — use a `_normalizer()` helper and a `_make_snapshot()` factory that builds `RepoStateSnapshot` with appropriate signal overrides.
- **One test file per goal**: Each deriver gets its own test file for clear ownership.
- **Patch module-level `Path` constants for `ProposalOutcomeDeriver`**: Since it uses module-level `_FEEDBACK_DIR` and `_PROPOSER_ROOT` constants (not constructor params), use `unittest.mock.patch` to redirect them to `tmp_path` subdirectories. `NoOpLoopDeriver` already accepts these as constructor kwargs — use those directly.

## Success Criteria

- All four new test files exist and pass with `pytest tests/test_ci_pattern_deriver.py tests/test_validation_pattern_deriver.py tests/test_proposal_outcome_deriver.py tests/test_noop_loop_deriver.py`.
- Combined test count across the four files is ≥23.
- No existing tests are broken: `pytest tests/` passes with the same result as before.
- Each test file imports only from `control_plane.insights.derivers.*`, `control_plane.insights.normalizer`, `control_plane.observer.models`, and standard library modules — no new external dependencies.
