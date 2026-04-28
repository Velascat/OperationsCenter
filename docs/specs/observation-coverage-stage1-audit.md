---
status: complete
created_at: 2026-04-28T00:00:00Z
scope:
  - src/operations_center/insights/derivers/observation_coverage.py
  - src/operations_center/decision/rules/observation_coverage.py
  - src/operations_center/observer/collectors/dependency_drift.py
  - src/operations_center/insights/derivers/dependency_drift.py
  - src/operations_center/decision/rules/dependency_drift.py
---

## Stage 1 Audit Summary

- 23 tests pass across the audited modules.
- Aggregate line and branch coverage is 95% across 5 modules.
- `collector`, `dependency_drift` deriver, and `dependency_drift` rule are at 100% coverage.
- Remaining gaps are isolated to `ObservationCoverageDeriver` and `ObservationCoverageRule`.

## Confirmed Gaps

### `ObservationCoverageDeriver` (`92%`)

- Missing empty-snapshots coverage for the early return path at line 16.
- Missing the branch where consecutive unavailable counting is interrupted when a signal becomes available mid-history at line 38.

### `ObservationCoverageRule` (`88%`)

- Missing wrong-kind filtering coverage at line 18.
- Missing the non-`persistent_unavailable` `dedup_key` branch that falls through at line 19 to the line 16 guard.

## Edge Cases Still Worth Verifying

- Collector behavior with non-dict entries in `statuses`, permission errors, and symlinked directories.
- Deriver behavior for mixed `available -> not_available -> available` histories and for exactly two consecutive `not_available` snapshots with no transition.
- Rule behavior for below-threshold `persistent_unavailable` inputs and the boundary between `medium` and `high` confidence.

## Stage 2 Test Plan

- Add 8 to 10 focused tests.
- Prioritize the four uncovered branches above first.
- Use the remaining tests on the listed edge cases to move all audited modules to at least 98% line and branch coverage.
