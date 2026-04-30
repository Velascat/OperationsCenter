---
status: implemented
---
# Insight Engine

The insight engine is the second stage of the autonomy pipeline. It converts retained repo observer snapshots into normalized, machine-readable findings.

It does **not**:

- create Plane tasks
- decide what work should happen next
- rank severity
- modify the repo

## Role In The Larger Autonomy Flow

```text
observe -> analyze -> decide -> propose
```

## Inputs

Primary input:

- `tools/report/operations_center/observer/<run_id>/repo_state_snapshot.json`

The insight engine also reads a bounded recent snapshot history for the same repo to detect continuity patterns.

## Derivers And Insight Kinds

| Deriver | Insight kinds emitted |
|---------|-----------------------|
| `DirtyTreeDeriver` | `dirty_tree/present` |
| `CommitActivityDeriver` | `commit_activity/low` |
| `FileHotspotsDeriver` | `file_hotspots/concentration` |
| `TestContinuityDeriver` | `test_status/persistently_unknown` |
| `DependencyDriftDeriver` | `dependency_drift/persistent` |
| `TodoConcentrationDeriver` | `todo_signal/high_concentration` |
| `ObservationCoverageDeriver` | `observation_coverage/gap` |
| `ExecutionHealthDeriver` | `execution_health/high_no_op_rate`, `execution_health/persistent_validation_failures` |
| `LintDriftDeriver` | `lint_drift/present`, `lint_drift/worsened` |
| `TypeHealthDeriver` | `type_health/present`, `type_health/worsened` |
| `CIPatternDeriver` | `ci_pattern/failing`, `ci_pattern/flaky` |
| `ValidationPatternDeriver` | `validation_pattern/repeated_failures` |
| `ProposalOutcomeDeriver` | `proposal_outcome/acceptance_rate_low`, `proposal_outcome/acceptance_rate_high` |
| `CrossRepoSynthesisDeriver` | `cross_repo/pattern_detected` |

## Deriver Details

### ExecutionHealthDeriver

Reads `signals.execution_health` and derives:

| Insight | Condition |
|---------|-----------|
| `high_no_op_rate` | ≥50% of runs were no-ops and `total_runs` ≥ 5 |
| `persistent_validation_failures` | `validation_failed_count` ≥ threshold (default 3, tunable) |

### LintDriftDeriver

Reads `signals.lint_signal` and derives:

| Insight | Condition |
|---------|-----------|
| `lint_drift/present` | `violation_count > 0` in the current snapshot |
| `lint_drift/worsened` | violation count increased from the prior snapshot |

### TypeHealthDeriver

Reads `signals.type_signal` and derives:

| Insight | Condition |
|---------|-----------|
| `type_health/present` | `error_count > 0` in the current snapshot |
| `type_health/worsened` | error count increased from the prior snapshot |

### CIPatternDeriver

Reads `signals.ci_history` and derives:

| Insight | Condition |
|---------|-----------|
| `ci_pattern/failing` | one or more checks in `failing_checks` |
| `ci_pattern/flaky` | one or more checks in `flaky_checks` |

### ValidationPatternDeriver

Reads `signals.validation_history` and derives:

| Insight | Condition |
|---------|-----------|
| `validation_pattern/repeated_failures` | one or more tasks in `tasks_with_repeated_failures` |

Evidence includes the top 3 affected task IDs, the worst task's failure details, and the overall failure rate.

### ProposalOutcomeDeriver

Reads retained feedback records from `state/proposal_feedback/` and derives:

| Insight | Condition |
|---------|-----------|
| `proposal_outcome/acceptance_rate_low` | acceptance rate < 30% with ≥5 feedback records |
| `proposal_outcome/acceptance_rate_high` | acceptance rate ≥ 80% with ≥5 feedback records |

These insights feed the self-tuning regulator's acceptance rate rules.

### CrossRepoSynthesisDeriver

Reads the latest `repo_insights.json` artifact for every repo found under `tools/report/operations_center/insights/`. Groups insight kinds across repos and derives:

| Insight | Condition |
|---------|-----------|
| `cross_repo/pattern_detected` | the same insight kind appears in ≥2 repos |

Evidence includes `shared_insight_kind`, `repo_count`, `repos` (list), and a description suggesting an org-wide fix. Falls back silently to zero insights when fewer than 2 repos have artifact data. The deriver is intentionally read-only and never modifies the repo.

## Output

Insight runs write retained artifacts under:

- `tools/report/operations_center/insights/<run_id>/repo_insights.json`
- `tools/report/operations_center/insights/<run_id>/repo_insights.md`

The JSON artifact is the primary machine-consumable contract for later decision logic.

## Design Constraints

- factual
- bounded
- deterministic
- read-only

The engine answers:

> what is observably happening?

It does not answer:

> what should we do about it?
