---
campaign_id: a1f3d72e-8b94-4c5f-ae17-6d0e3b8c91f4
slug: test-untested-decision-rules
phases:
  - implement
  - test
  - improve
repos:
  - control-plane
area_keywords:
  - decision/rules
  - hotspot_concentration
  - lint_cluster
  - observation_coverage
  - todo_accumulation
  - coverage_gap
  - ci_pattern
  - dependency_drift
  - validation_pattern
status: active
created_at: 2026-04-20T18:00:00Z
---

## Overview

Eight of the fourteen decision rules under `src/control_plane/decision/rules/` have zero test coverage: `HotspotConcentrationRule`, `LintClusterRule`, `ObservationCoverageRule`, `TodoAccumulationRule`, `CoverageGapRule`, `CIPatternRule`, `DependencyDriftRule`, and `ValidationPatternRule`. These rules total ~400 lines of production logic that convert `DerivedInsight` objects into `CandidateSpec` proposals. This campaign adds focused unit tests for each rule covering all code paths, filtering logic, and edge cases.

## Goals

1. **Add `tests/test_decision_rules_structural.py` covering `HotspotConcentrationRule`, `LintClusterRule`, and `CoverageGapRule`**: Build `DerivedInsight` objects in-memory and pass them through each rule's `evaluate()` method. For `HotspotConcentrationRule`: test that insights with kind != `file_hotspot` are ignored, that the `min_repeated_runs` threshold filters correctly, that `dominant_current` evidence merges into the candidate, and that multiple subjects produce sorted candidates. For `LintClusterRule`: test `theme/lint_cluster` and `theme/type_cluster` each produce a candidate with correct family/pattern_key, that unrelated insight kinds are ignored, and that both insight kinds can coexist in a single evaluate call. For `CoverageGapRule`: test `coverage_gap/low_overall` and `coverage_gap/uncovered_files` each emit a candidate, that `top_uncovered` is truncated to 3 in the evidence line, and that unrelated kinds produce no candidates. Target: >=12 test cases.

2. **Add `tests/test_decision_rules_continuity.py` covering `ObservationCoverageRule`, `DependencyDriftRule`, and `TodoAccumulationRule`**: These three rules share a common pattern of threshold-gated continuity detection. For `ObservationCoverageRule`: test that non-`observation_coverage` kinds are ignored, that `consecutive_snapshots` below `min_consecutive_runs` produces no candidate, that meeting/exceeding threshold produces a candidate, and that confidence is `"high"` when consecutive >= 3 and `"medium"` otherwise. For `DependencyDriftRule`: test that all three filter conditions (kind, subject, dedup_key suffix) must match, that `min_consecutive_runs` threshold gates emission, and that confidence is `"high"` at >= 4 consecutive snapshots. For `TodoAccumulationRule`: test `count_changed` dedup_key with `current_total > previous_total` emits a candidate, that `current_total <= previous_total` does not, that `fixme|present` dedup_key emits an independent candidate, and that both paths can fire from a single evaluate call. Target: >=12 test cases.

3. **Add `tests/test_decision_rules_ci_validation.py` covering `CIPatternRule` and `ValidationPatternRule`**: For `CIPatternRule`: test that non-`ci_pattern` kinds are ignored, that `status == "failing"` emits a candidate with confidence `"high"` and correct evidence keys (`failing_checks`, `failure_rate`), that `status == "flaky"` emits a candidate with confidence `"medium"`, and that both statuses can fire from a single batch. For `ValidationPatternRule`: test that non-`validation_pattern` kinds are ignored, that `status == "repeated_failures"` emits a candidate, that confidence is `"high"` when `tasks_with_repeated_failures >= 3` and `"medium"` otherwise, and that `worst_task_id` is truncated to 8 chars in the evidence line. Target: >=8 test cases.

## Constraints

- **No production code changes**: This campaign is test-only. Do not modify any files under `src/`.
- **Construct `DerivedInsight` objects directly**: Use the Pydantic model from `control_plane.insights.models.DerivedInsight` with appropriate field values. Use `datetime.now()` or a fixed datetime for timestamp fields.
- **Follow existing test patterns**: Mirror the assertion style in `tests/test_execution_health.py` — construct insights, call `rule.evaluate(insights)`, assert on candidate count, family, pattern_key, confidence, and evidence_lines.
- **One test file per goal**: Three test files total, grouped by rule similarity for maintainability.
- **Import only from `control_plane.decision.rules.*`, `control_plane.decision.candidate_builder`, `control_plane.insights.models`, and standard library modules.** No new external dependencies.

## Success Criteria

- All three new test files exist and pass with `pytest tests/test_decision_rules_structural.py tests/test_decision_rules_continuity.py tests/test_decision_rules_ci_validation.py`.
- Combined test count across the three files is >= 32.
- No existing tests are broken: `pytest tests/` passes with the same result as before.
- Every code branch in the eight rules (each `if`/`elif` path in `evaluate()`) is exercised by at least one test.
