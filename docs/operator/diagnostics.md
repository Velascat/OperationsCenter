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

## Credential Validation and Expiry Detection

On the first cycle of each watcher run, Control Plane validates GitHub and Plane tokens.

**Invalid tokens (401/403):** The watcher logs `credential_invalid`, records an escalation event, and exits. If a watcher fails to start with `watch_credential_failure`, check that `GITHUB_TOKEN` and `PLANE_API_TOKEN` are set and valid for the configured workspace.

**Upcoming expiry (fine-grained PATs):** If the GitHub `/user` response includes an `x-token-expiration` header, Control Plane checks whether expiry is within `escalation.credential_expiry_warn_days` days (default 7). When approaching expiry:

- `≤ warn_days` remaining: logs `credential_expiry_soon` warning with days remaining and expiry date
- `≤ 1 day` remaining: logs error and records a `credential_github_expiring` escalation event

Set `escalation.credential_expiry_warn_days: 0` to disable expiry monitoring. Only fine-grained GitHub PATs expose this header; classic tokens do not.

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

## Failure Classification Reference

`classify_execution_result` maps execution failures to one of these classifications (checked in priority order):

| Classification | Trigger | Follow-up action |
|---------------|---------|-----------------|
| `scope_policy` | `allowed_paths` violation | policy-retry fired |
| `oom` | Out of memory / killed | investigate memory pressure |
| `timeout` | Process timed out | increase `kodo.timeout_seconds` or split task |
| `model_error` | API 5xx / overloaded | transient; retry usually succeeds |
| `context_limit` | Token limit exceeded | split task with `prior_progress` handoff |
| `dependency_missing` | ModuleNotFoundError / command not found | fix bootstrap |
| `flaky_test` | Known-flaky command | stabilise the test |
| `validation_failure` | Tests / lint fail | investigate test output |
| `tool_failure` | Bash/git tool error | investigate tool configuration |
| `infra_tooling` | Auth / missing file | fix credentials or environment |
| `unknown` | None of the above | investigate stderr |

## Self-Healing Log Events

When a task is blocked for the third consecutive time without a successful execution in between, the system posts a `[Improve] Repeated-block self-healing triggered` comment and logs:

```json
{"event": "self_healing_repeated_block", "task_id": "...", "consecutive_blocks": 3, "classification": "..."}
```

This means the task needs human review — autonomous retries for it are paused. The consecutive block counter resets after a successful execution.

## Cross-Repo Impact Warnings

When a goal task touches paths declared in another repo's `impact_report_paths`, a warning is logged:

```json
{"event": "cross_repo_impact_detected", "task_id": "...", "warnings": ["repo=shared_lib shared_path=src/api/ changed_file=src/api/client.py"]}
```

And a comment is posted on the task: `[Goal] Cross-repo impact detected`. This is advisory — verify dependent repos still build and pass tests.

## Supervisor Status

When using the process supervisor, check its status:

```bash
cat logs/local/supervisor.status.json
```

Fields per process: `role`, `alive`, `pid`, `restart_count`, `last_restart_at`. A high `restart_count` on a role indicates a crash loop — investigate the watcher log for that role.

## Circuit Breaker Escalation

When the circuit breaker trips (≥80% failure over last 5 executions) AND an escalation webhook is configured, a webhook POST is sent automatically (cooldown-guarded). Look for:

```json
{"event": "circuit_breaker_escalation_sent", "role": "...", "reason": "circuit_breaker_open"}
```

in the watcher log. The escalation fires once per cooldown period (`escalation.cooldown_seconds`, default 3600). The circuit breaker still resets when the failure rate improves — the escalation is informational.

## Suggested Debugging Order

1. `watch-all-status`
2. watcher log in `logs/local/watch-all/`
3. Plane comments on the task
4. retained artifact directory in `tools/report/kodo_plane/`
5. `plane-doctor` if the board/API contract looks wrong
6. heartbeat check: `python -m control_plane.entrypoints.worker.main heartbeat-check`
7. supervisor status: `cat logs/local/supervisor.status.json` (if using supervisor)
8. config drift: look for `config_drift_detected` in watcher log at cycle 1
9. workspace health: look for `workspace_health_*` events in improve watcher log
10. circuit breaker: look for `reason: circuit_breaker_open` in watcher log; check for `circuit_breaker_escalation_sent`
11. connection backoff: look for `watch_error` with `consecutive_errors > 1` in watcher log
12. quota exhaustion: look for `"kind": "kodo_quota_event"` in usage store
13. quality erosion: look for `"kind": "kodo_quality_warning"` in usage store
14. scope violations: look for `"kind": "scope_violation"` in usage store
15. board saturation: look for `"event": "propose_skipped_board_saturated"` in propose watcher log
16. self-healing: look for `self_healing_repeated_block` events in improve watcher log
17. credential expiry: look for `credential_expiry_soon` in watcher log at cycle 1
18. cross-repo impact: look for `cross_repo_impact_detected` in goal watcher log
19. dependency updates: look for `dependency_update_task_created` in improve watcher log
20. quality trends: look for `quality_trend/lint_degrading` or `type_degrading` insights in `tools/report/control_plane/insights/`
21. confidence calibration: run `tune-autonomy` and check the calibration table for ⚠ families
22. error ingest: look for `error_ingest_task_created` events; check `state/error_ingest_dedup.json` for dedup state

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

## Quality Trend Warnings

The `QualityTrendDeriver` emits insights when lint or type error counts are trending in a direction across ≥3 observer snapshots. Check the latest insights artifact for these signals:

```bash
cat tools/report/control_plane/insights/$(ls -t tools/report/control_plane/insights/ | head -1)
```

Look for entries with `kind` starting with `quality_trend/`:

| Insight | Meaning |
|---------|---------|
| `quality_trend/lint_degrading` | Lint errors increased >10% from oldest to newest snapshot |
| `quality_trend/type_degrading` | Type errors increased >10% |
| `quality_trend/lint_improving` | Lint errors decreased >10% |
| `quality_trend/type_improving` | Type errors decreased >10% |
| `quality_trend/stagnant` | Metrics present but <10% change in either direction |

`stagnant` on a repo with many outstanding lint/type tasks may indicate the autonomy loop is proposing tasks that are not reaching execution, or that tasks complete but introduce equivalent new violations.

## Confidence Calibration

After enough feedback records accumulate (≥5 per family/confidence combination), `tune-autonomy` prints a calibration table:

```
  Confidence calibration:
  family                       conf     n  accept%  expected    ratio
  lint_fix                     high     8    62.5%     80.0%   0.78
  type_fix                     high     6    33.3%     80.0%   0.42⚠
```

The `ratio` column is `acceptance_rate / expected_rate`. Interpretation:
- `ratio ≥ 0.9` (✓) — well-calibrated; the confidence label matches observed outcomes
- `0.6 ≤ ratio < 0.9` — mildly over-confident; monitor but no immediate action needed
- `ratio < 0.6` (⚠) — over-confident; the system is creating `high`-confidence proposals that are frequently rejected

When a family shows ⚠, consider lowering its `min_confidence` threshold in config or demoting its tier until the acceptance rate recovers.

To record calibration data manually:

```bash
python -m control_plane.entrypoints.feedback.main record \
    --task-id <uuid> --outcome merged \
    --family lint_fix --confidence high
```

Calibration data is stored in `state/calibration_store.json`.

## Maintenance Boundary

Normal execution should stay pinned and stable.

Use diagnostics and maintenance commands to:

- inspect health
- investigate failures
- check version drift

Do not treat normal `run` or `watch` cycles as the place to auto-upgrade tooling.

Likewise, do not treat the proposer lane as unlimited self-directed work generation. It is intentionally bounded by cooldown, quota, and deduplication guardrails.
