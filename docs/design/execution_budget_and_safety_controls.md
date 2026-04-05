# Execution Budget And Safety Controls

Control Plane now enforces a local execution control layer before expensive worker actions run.

## What Is Controlled

- rolling execution budget across hour and day windows
- bounded retry count per task for `goal` and `test`
- watcher-side no-op suppression for unchanged task signatures
- proposal suppression when remaining execution budget is too low
- conservative decision-family gating for early autonomy
- explicit dry-run support for decision/proposer CLI flows

## Operator Knobs

- `CONTROL_PLANE_MAX_EXEC_PER_HOUR`
- `CONTROL_PLANE_MAX_EXEC_PER_DAY`
- `CONTROL_PLANE_MAX_RETRIES_PER_TASK`
- `CONTROL_PLANE_MIN_REMAINING_EXEC_FOR_PROPOSALS`
- `CONTROL_PLANE_WATCH_INTERVAL_GOAL_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_TEST_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_IMPROVE_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_PROPOSE_SECONDS`

## Retained Artifacts

- execution usage ledger: `tools/report/control_plane/execution/usage.json`
- per-run control outcome: `control_outcome.json` inside retained run directories

## Current Conservative Proposal Policy

Allowed initially:

- `observation_coverage`
- `test_visibility`
- `dependency_drift_followup`

Deferred initially:

- `hotspot_concentration`
- `todo_accumulation`

Those deferred families are not dropped silently. They are emitted as suppressed candidates with explicit reasons in decision artifacts.
