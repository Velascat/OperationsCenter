---
status: implemented
---
# Execution Budget And Safety Controls

Operations Center now enforces a local execution control layer before expensive worker actions run.

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
| `OPERATIONS_CENTER_CIRCUIT_BREAKER_THRESHOLD` | `0.8` | Failure fraction that opens the circuit |
| `OPERATIONS_CENTER_CIRCUIT_BREAKER_WINDOW` | `5` | Number of recent outcomes to sample |

The window requires ≥3 samples before the circuit can open (startup safety margin).

## Disk Space Guardrail

Before writing to the usage store, `_check_disk_space(path)` is called using `shutil.disk_usage()`:

- **below 50 MB free**: raises `OSError` — the write is blocked
- **below 200 MB free**: logs `"event": "disk_space_low"` warning but continues

The same check also runs in `autonomy-cycle` before writing the cycle report. This prevents silent data loss on full disks.

## Board Saturation Backpressure

Before the propose watcher or `autonomy-cycle` creates new tasks, it counts open tasks labeled `source: autonomy` in `Ready for AI` and `Backlog`. If the count meets or exceeds `MAX_QUEUED_AUTONOMY_TASKS`, the propose stage is skipped entirely for that cycle.

Default: 15. Configurable via `OPERATIONS_CENTER_MAX_QUEUED_AUTONOMY_TASKS`.

## Quality Erosion Tracking

After each Kodo run, the execution service scans added diff lines (`+` prefix) for quality suppressions:

- `# noqa` additions
- `# type: ignore` additions
- bare `pass` statement additions

When the total meets or exceeds 3, a `kodo_quality_warning` event is written to the usage store with the suppression counts. This does not affect task status or circuit breaker state — it is observability-only.

## Scope Violation Recording

When a Kodo run modifies files outside `allowed_paths`, the violations are enforced (branch not pushed) and a `scope_violation` event is written to the usage store with `violated_files` and `repo_key`. This is observability alongside the enforcement that was already in place.

## Quota Exhaustion Bypass

Hard quota exhaustion (`is_quota_exhausted(result)` is true) is treated differently from ordinary failures:

- calls `usage_store.record_quota_event(backend=...)` instead of `record_execution_outcome()`
- does **not** feed the circuit breaker window (quota exhaustion is an infrastructure event, not a code quality signal)
- task is moved to `Blocked` with `blocked_classification: quota_exhausted`

## Operator Knobs

- `OPERATIONS_CENTER_MAX_EXEC_PER_HOUR`
- `OPERATIONS_CENTER_MAX_EXEC_PER_DAY`
- `OPERATIONS_CENTER_MAX_RETRIES_PER_TASK`
- `OPERATIONS_CENTER_MIN_REMAINING_EXEC_FOR_PROPOSALS`
- `OPERATIONS_CENTER_CIRCUIT_BREAKER_THRESHOLD`
- `OPERATIONS_CENTER_CIRCUIT_BREAKER_WINDOW`
- `OPERATIONS_CENTER_MAX_QUEUED_AUTONOMY_TASKS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_GOAL_SECONDS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_TEST_SECONDS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_IMPROVE_SECONDS`
- `OPERATIONS_CENTER_WATCH_INTERVAL_PROPOSE_SECONDS`

## Retained Artifacts

- execution usage ledger: `tools/report/operations_center/execution/usage.json`
- per-run control outcome: `control_outcome.json` inside retained run directories
- proposal rejection store: `state/proposal_rejections.json`

Usage store event kinds:
- `execution_outcome` — normal success/failure (feeds circuit breaker)
- `quota_event` — hard quota exhaustion (does not feed circuit breaker)
- `kodo_quality_warning` — quality suppression count above threshold
- `scope_violation` — changed files outside `allowed_paths`

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
