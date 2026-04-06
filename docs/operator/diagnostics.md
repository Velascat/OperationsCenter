# Diagnostics And Maintenance

This guide covers the operator-facing checks that help explain what the local system is doing and why.

## Plane Doctor

```bash
./scripts/control-plane.sh plane-doctor --task-id TASK-123
```

Use this to verify:

- Plane base URL
- configured workspace/project
- API user identity
- project and work-item endpoint reachability

This is the fastest check when polling is not finding work.

## Plane Smoke Test

```bash
./scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
```

Use this to verify Plane fetch/comment behavior without running a full Kodo task.

Retained smoke artifacts are written under:

- `tools/report/kodo_plane/<timestamp>_<task_id>_<run_id>/`

## Providers Status

```bash
./scripts/control-plane.sh providers-status
```

Use this to re-check:

- installed provider CLIs
- versions
- auth readiness
- headless readiness where supported

## Dependency Check

```bash
./scripts/control-plane.sh dependency-check
./scripts/control-plane.sh dependency-check --create-plane-tasks
```

This maintenance checker:

- reads local version pins
- checks installed/local health
- compares upstream latest where practical
- writes a retained report
- can create Plane `task-kind: improve` tasks for drift or breakage

This is a maintenance path, not part of normal task execution.

## Retained Summaries

Retained `result_summary.md` files align with board/log language and include:

- `worker_role`
- `task_kind`
- `run_id`
- `final_status`
- `blocked_classification`
- `follow_up_task_ids`
- `human_attention_required` when relevant

## Watcher Heartbeat Check

```bash
python -m control_plane.entrypoints.worker.main heartbeat-check --log-dir logs/local/watch-all
```

Returns exit code 0 if all watchers wrote a heartbeat within the last 5 minutes. Returns exit code 1 with a message listing stale roles. Run from cron to get paged when a watcher dies silently.

## Credential Validation

On the first cycle of each watcher run, Control Plane calls the GitHub and Plane APIs to validate tokens. If either returns 401/403, the watcher logs a clear error and exits rather than running with invalid credentials. An escalation event is also written to the usage store so the pattern shows up in the dashboard.

If a watcher fails to start with `watch_credential_failure`, check:
- `GITHUB_TOKEN` is set and valid
- `PLANE_API_TOKEN` is set and valid for the configured workspace

## Config Schema Drift Check

At watcher startup (cycle 1), Control Plane compares your deployed config against
`config/control_plane.example.yaml`.  If any top-level or nested key in the example is
absent from your config, it is logged as a `config_drift_detected` warning:

```
{"event": "config_drift_detected", "missing_key": "escalation", ...}
{"event": "config_drift_summary", "missing_count": 2, "missing_keys": ["escalation", "stale_pr_days"]}
```

This fires on every watcher start until the gap is resolved.  Check the watcher log if
a feature appears to be silently disabled.

## Workspace Health Check

The improve watcher automatically verifies and repairs repo environments every 25 cycles.
To check manually:

1. Look for `workspace_health_unhealthy` and `workspace_health_repair_failed` events in the improve watcher log.
2. If a `[Workspace] Repair environment for <repo>` task appears on the board, the automatic repair failed — investigate the venv or bootstrap script for that repo.

## Spend Report

View execution count and estimated cost for the last N days:

```bash
# Last 24 hours
python -m control_plane.entrypoints.worker.main spend-report

# Last 7 days
python -m control_plane.entrypoints.worker.main spend-report --window-days 7
```

Returns JSON:
```json
{
  "window_days": 7,
  "total_executions": 42,
  "total_estimated_usd": 6.30,
  "per_repo": {
    "ControlPlane": {"executions": 18, "estimated_usd": 2.70},
    "code_youtube_shorts": {"executions": 24, "estimated_usd": 3.60}
  }
}
```

Requires `cost_per_execution_usd` to be set in config (default 0.0 = disabled).

## Circuit Breaker Diagnosis

When a systemic failure (bad kodo version, auth regression) causes every execution to fail, the circuit breaker opens to prevent burning the full daily budget. Look for:

```
{"event": "budget_decision", "allowed": false, "reason": "circuit_breaker_open", ...}
```

in the watcher log. The circuit reopens automatically when the failure rate drops below 80% over the last 5 executions. To reset immediately: fix the underlying issue and wait for a successful execution.

Thresholds are tunable via env vars:
- `CONTROL_PLANE_CIRCUIT_BREAKER_THRESHOLD` (default `0.8`)
- `CONTROL_PLANE_CIRCUIT_BREAKER_WINDOW` (default `5`)

## Connection Error Backoff

Transient network failures (Plane API down, DNS failure) now trigger exponential backoff in the watcher. Look for `"event": "watch_error"` with `"consecutive_errors": N` and `"backoff_seconds": N` in the log. The backoff caps at 5 minutes. The counter resets on the next successful cycle.

If `consecutive_errors` is climbing and not resetting, the Plane API is unreachable — check network or the `PLANE_API_TOKEN`.

## Observer Snapshot Staleness

`generate-insights` warns if the most recent observer snapshot is older than 2 hours:

```
[warn] Latest observer snapshot is 4.2h old — insights may not reflect current repo state.
```

If you see this, re-run `observe-repo` before generating insights:

```bash
./scripts/control-plane.sh observe-repo
./scripts/control-plane.sh generate-insights
```

## Proposer Quiet Diagnosis

When the proposer emits 0 candidates for 5 or more consecutive cycles, a diagnosis file is written automatically:

```
logs/autonomy_cycle/quiet_diagnosis.json
```

It contains:
- `cycles_analyzed` — how many recent cycles were checked
- `suppression_reasons` — reason counts across all cycles, sorted by frequency
- `advice` — a plain-language summary of the dominant suppression reason

Check this file before manually inspecting individual cycle JSON files.

The file is deleted automatically when the proposer starts emitting again.

## Proposal Rejection Store

To see which autonomy candidates have been permanently rejected (by human cancellation):

```bash
cat state/proposal_rejections.json
```

Each entry has `reason`, `task_id`, `task_title`, and `recorded_at`. These are checked before budget, cooldown, or dedup — a rejected key will never be proposed again. To allow a re-proposal, delete the entry from the JSON file and rerun `autonomy-cycle`.

## Quality Erosion Warnings

After each Kodo run, the execution service scans the diff for quality-suppressing additions:

- `# noqa` annotations
- `# type: ignore` annotations
- bare `pass` statements

When the combined total reaches or exceeds 3, a `kodo_quality_warning` event is written to the usage store and a note is appended to the PR comment:

```
> [quality] This run added N quality suppressions: {"noqa": N, "type_ignore": N}. Review before merging.
```

To audit suppression trends, filter the usage store for `"kind": "kodo_quality_warning"` entries. These are tracked for observability only — they do not affect task status.

## Scope Violation Observability

When `allowed_paths` is configured and a Kodo run modifies files outside the allowed set, the policy is enforced (changes are not pushed) and a `scope_violation` event is written to the usage store. Fields include `violated_files` and `repo_key`.

Filter for `"kind": "scope_violation"` in `tools/report/control_plane/execution/usage.json` to see which tasks have exceeded their path budget. Recurring violations may indicate the `allowed_paths` config is too narrow for the goal.

## Quota Exhaustion Detection

Hard quota exhaustion from the Kodo orchestrator (e.g. Anthropic API quota exceeded) is detected separately from rate limiting. When detected:

- a `kodo_quota_event` is written to the usage store (does **not** feed the circuit breaker)
- the task is moved to `Blocked` with `blocked_classification: quota_exhausted`

Filter for `"kind": "kodo_quota_event"` to track frequency. Unlike transient rate limits, quota exhaustion typically requires manual intervention (quota increase or wait for reset).

## Disk Space Check

See the [Disk Space Guardrail](../operator/runtime.md#disk-space-guardrail) section in the Runtime Guide. If writes to the usage store are failing with `OSError`, disk space is the first thing to check.

## Suggested Debugging Order

1. `watch-all-status`
2. watcher log in `logs/local/watch-all/`
3. Plane comments on the task
4. retained artifact directory in `tools/report/kodo_plane/`
5. `plane-doctor` if the board/API contract looks wrong
6. heartbeat check: `python -m control_plane.entrypoints.worker.main heartbeat-check`
7. config drift: look for `config_drift_detected` in watcher log at cycle 1
8. workspace health: look for `workspace_health_*` events in improve watcher log
9. circuit breaker: look for `reason: circuit_breaker_open` in watcher log
10. connection backoff: look for `watch_error` with `consecutive_errors > 1` in watcher log
11. quota exhaustion: look for `"kind": "kodo_quota_event"` in usage store
12. quality erosion: look for `"kind": "kodo_quality_warning"` in usage store
13. scope violations: look for `"kind": "scope_violation"` in usage store
14. board saturation: look for `"event": "propose_skipped_board_saturated"` in propose watcher log

For autonomy-layer inputs:

- `./scripts/control-plane.sh observe-repo`
- `./scripts/control-plane.sh generate-insights`
- `./scripts/control-plane.sh decide-proposals`
- `./scripts/control-plane.sh propose-from-candidates --dry-run`
- retained observer artifacts in `tools/report/control_plane/observer/`
- retained insight artifacts in `tools/report/control_plane/insights/`
- retained decision artifacts in `tools/report/control_plane/decision/`
- retained proposer artifacts in `tools/report/control_plane/proposer/`

When the board is quiet, also check the proposer lane:

- proposer heartbeat/status in `watch-all-status`
- proposer log in `logs/local/watch-all/`
- new tasks labeled `source: proposer`
- `logs/autonomy_cycle/quiet_diagnosis.json` for aggregated suppression reasons

## Maintenance Boundary

Normal execution should stay pinned and stable.

Use diagnostics and maintenance commands to:

- inspect health
- investigate failures
- check version drift

Do not treat normal `run` or `watch` cycles as the place to auto-upgrade tooling.

Likewise, do not treat the proposer lane as unlimited self-directed work generation. It is intentionally bounded by cooldown, quota, and deduplication guardrails.
