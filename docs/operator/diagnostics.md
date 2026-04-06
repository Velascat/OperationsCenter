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

## Suggested Debugging Order

1. `watch-all-status`
2. watcher log in `logs/local/watch-all/`
3. Plane comments on the task
4. retained artifact directory in `tools/report/kodo_plane/`
5. `plane-doctor` if the board/API contract looks wrong
6. heartbeat check: `python -m control_plane.entrypoints.worker.main heartbeat-check`
7. config drift: look for `config_drift_detected` in watcher log at cycle 1
8. workspace health: look for `workspace_health_*` events in improve watcher log

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

## Maintenance Boundary

Normal execution should stay pinned and stable.

Use diagnostics and maintenance commands to:

- inspect health
- investigate failures
- check version drift

Do not treat normal `run` or `watch` cycles as the place to auto-upgrade tooling.

Likewise, do not treat the proposer lane as unlimited self-directed work generation. It is intentionally bounded by cooldown, quota, and deduplication guardrails.
