# Execution Budget And Safety Controls

Control Plane now enforces a local execution control layer before expensive worker actions run.

## What Is Controlled

- rolling execution budget across hour and day windows
- bounded retry count per task for `goal` and `test`
- watcher-side no-op suppression for unchanged task signatures
- proposal suppression when remaining execution budget is too low
- conservative decision-family gating for early autonomy
- explicit dry-run support for decision/proposer CLI flows

## Circuit Breaker

Beyond the rolling count budget, a success-rate circuit breaker opens when ≥80% of the last 5 task executions failed. When open, `budget_decision` returns `reason="circuit_breaker_open"` and no further tasks run until the failure rate improves. This prevents burning the entire hourly or daily budget when something systemic breaks (bad kodo version, auth regression, etc.).

`record_execution_outcome(*, task_id, role, succeeded, now)` is called after each `handle_goal_task` and `handle_test_task` completes to feed the circuit breaker window.

Both thresholds are tunable:

| Env var | Default | Effect |
|---------|---------|--------|
| `CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD` | `0.8` | Failure fraction that opens the circuit |
| `CONTROL_PLANE_CIRCUIT_BREAKER_WINDOW` | `5` | Number of recent outcomes to sample |

The window requires ≥3 samples before the circuit can open (startup safety margin).

## Operator Knobs

- `CONTROL_PLANE_MAX_EXEC_PER_HOUR`
- `CONTROL_PLANE_MAX_EXEC_PER_DAY`
- `CONTROL_PLANE_MAX_RETRIES_PER_TASK`
- `CONTROL_PLANE_MIN_REMAINING_EXEC_FOR_PROPOSALS`
- `CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD`
- `CONTROL_PLANE_CIRCUIT_BREAKER_WINDOW`
- `CONTROL_PLANE_WATCH_INTERVAL_GOAL_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_TEST_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_IMPROVE_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_PROPOSE_SECONDS`

## Retained Artifacts

- execution usage ledger: `tools/report/control_plane/execution/usage.json`
- per-run control outcome: `control_outcome.json` inside retained run directories
- proposal rejection store: `state/proposal_rejections.json`

## Execution Profiles Per Task Kind

Every Kodo invocation can use a different resource budget depending on task type. `kodo_profiles` in config accepts per-task-kind overrides keyed by `execution_mode` (e.g. `"goal"`, `"improve"`, `"test"`) or a special `"default"` key:

```yaml
kodo_profiles:
  lint_fix:
    cycles: 2
    exchanges: 10
    effort: low
  context_limit:
    cycles: 6
    exchanges: 40
    effort: high
  default:
    cycles: 4
    exchanges: 25
    effort: medium
```

The profile is resolved by `execution_mode` first, falling back to `"default"` if present, then to the base kodo config. This prevents lint tasks from burning the same token budget as complex refactors.

## Current Conservative Proposal Policy

Allowed initially:

- `observation_coverage`
- `test_visibility`
- `dependency_drift_followup`

Deferred initially:

- `hotspot_concentration`
- `todo_accumulation`

Those deferred families are not dropped silently. They are emitted as suppressed candidates with explicit reasons in decision artifacts.
