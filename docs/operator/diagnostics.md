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
    "VideoFoundry": {"executions": 24, "estimated_usd": 3.60}
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
| `awaiting_input` | Kodo embedded `<!-- cp:question: ... -->` | human must reply; improve watcher re-queues automatically |
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

## Campaign Status

Track multi-step plan progress from the board:

```bash
# Show all active campaigns
python -m control_plane.entrypoints.campaign_status.main

# Show only in-progress campaigns
python -m control_plane.entrypoints.campaign_status.main --status in_progress

# JSON output for scripting
python -m control_plane.entrypoints.campaign_status.main --json
```

Campaigns are created automatically when the improve watcher decomposes a multi-step plan (tasks with titles containing `refactor`, `migrate`, `redesign`, etc. or labeled `plan: multi-step`). Each campaign tracks step completion and overall progress.

Campaign records are stored in `state/campaigns.json`. Each record contains `campaign_id`, `title`, `step_ids`, `done_step_ids`, `cancelled_step_ids`, `total_steps`, `completed_steps`, `progress_pct`, and `status` (`pending`, `in_progress`, `complete`, `partially_cancelled`).

## Awaiting-Input Tasks

When Kodo embeds `<!-- cp:question: ... -->` in its output, the task is classified as `awaiting_input` and blocked. The improve watcher:

1. Extracts the question text from the HTML comment.
2. Posts it as a Plane comment asking for human input.
3. Scans every 8 improve cycles for a human reply.
4. When a reply is detected, injects the answer into the task description and re-queues it to `Ready for AI`.

To find pending awaiting-input tasks:

```bash
grep "awaiting_input" logs/local/watch-all/improve.log | tail -20
```

Or check the Plane board for tasks with `blocked_classification: awaiting_input` in their latest comment.

## CI Webhook

The CI webhook server receives GitHub check-run events and triggers autonomy cycles reactively:

```bash
# Start the webhook server
python -m control_plane.entrypoints.ci_webhook.main --port 8765 --secret "$WEBHOOK_SECRET"

# Trigger files land in state/ci_webhook_triggers/
ls state/ci_webhook_triggers/
```

Each trigger file is named `<timestamp>_<check_suite_id>.json` and contains the repository, branch, check name, status, and conclusion. The pipeline trigger watcher watches this directory and fires `autonomy-cycle` when a new trigger appears.

HMAC-SHA256 signature validation (`X-Hub-Signature-256` header) is enforced when `--secret` is provided. Requests without a valid signature return HTTP 401.

## Stray Artifact Isolation

Self-review verdict files must land inside the task's ephemeral workspace (`/tmp/cp-task-<id>/`), not in `$HOME` or the repo root. The prompt uses an absolute path and the supervisor starts each watcher with `cd <ROOT_DIR>` so relative paths in the worker process resolve correctly.

If you find `.review/` directories or `*_verdict.txt` files in unexpected locations (`$HOME`, bare `/home/dev/repo/`, unrelated repo clones), they are leftovers from before this fix. You can safely delete them:

```bash
find $HOME -maxdepth 2 -name '*verdict*' -o -name '.review' -type d 2>/dev/null
```

The root cause of stray verdicts is either an old kodo run that predates the absolute-path fix, or a supervisor that was not starting the worker process from `ROOT_DIR`. Both are fixed; this should not recur.

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
16. propose backlog gate: look for `"event": "watch_propose_skipped_backlog"` in propose watcher log — board has ≥ `propose_skip_when_ready_count` ready tasks
17. kodo concurrency gate: look for `"event": "watch_skip_kodo_gate"` with `"reason": "kodo_concurrency_cap"` — another kodo run is active
18. memory gate: look for `"event": "watch_skip_kodo_gate"` with `"reason": "low_memory"` — less than `min_kodo_available_mb` free
19. self-healing: look for `self_healing_repeated_block` events in improve watcher log
20. credential expiry: look for `credential_expiry_soon` in watcher log at cycle 1
21. cross-repo impact: look for `cross_repo_impact_detected` in goal watcher log
22. dependency updates: look for `dependency_update_task_created` in improve watcher log
23. quality trends: look for `quality_trend/lint_degrading` or `type_degrading` insights in `tools/report/control_plane/insights/`
24. confidence calibration: run `tune-autonomy` and check the calibration table for ⚠ families
25. error ingest: look for `error_ingest_task_created` events; check `state/error_ingest_dedup.json` for dedup state
26. no-op loop: look for `noop_loop/family_cycling` in the latest insights artifact; family is cycling without acceptance
27. coverage gap: look for `coverage_gap/low_overall` or `coverage_gap/uncovered_files` insights; check `coverage.xml` is being generated
28. execution env: look for `execution_env_warning` in the goal watcher log; check that required tools are installed in venv
29. awaiting-input tasks: look for `blocked_classification: awaiting_input` in improve watcher log; check Plane task for extracted question
30. priority rescore: look for `priority_rescore_demoted` and `priority_rescore_promoted` events in improve watcher log every 45 cycles
31. cross-repo patterns: look for `cross_repo/pattern_detected` in the latest insights artifact; consider org-wide fix tasks
32. campaign status: run `campaign-status` CLI; check `state/campaigns.json` for stalled campaigns
33. ci webhook triggers: check `state/ci_webhook_triggers/` for unprocessed trigger files
34. orphaned workspaces: look for `watch_cleanup_orphaned_workspaces` at cycle 1 or every 20 cycles; leftover `/tmp/cp-task-*` dirs indicate prior worker crashes

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

## No-Op Loop Warnings

When the `NoOpLoopDeriver` detects a family cycling without acceptance, a `noop_loop/family_cycling` insight is written. Check:

```bash
cat tools/report/control_plane/insights/$(ls -t tools/report/control_plane/insights/ | head -1) | python3 -m json.tool | grep -A5 noop_loop
```

Evidence fields: `family`, `proposals_in_window`, `merges_in_window`, `look_back_days`.

**Common causes:**
- The family's threshold is too low — it fires too easily on signals that don't warrant a fix
- The generated proposals are consistently rejected without a clear path to acceptance (check `state/rejection_patterns.json`)
- The execution environment is missing the required tool (check `execution_env_warning` in the goal watcher log)

**Remediation:** Consider raising the family's signal threshold in `config/autonomy_tuning.json`, or demoting the family's tier via `autonomy-tiers set --family <family> --tier 0`.

## Coverage Gap Detection

When `coverage.xml`, a text coverage report, or `htmlcov/index.html` is present in a repo, the observer collects coverage data automatically. Check whether coverage reports exist:

```bash
ls <repo_path>/coverage.xml <repo_path>/htmlcov/index.html 2>/dev/null
```

If coverage data is available but no `coverage_gap` proposals are appearing, the total may be above the 60% threshold or the uncovered file count may be below 3. Check the latest observer artifact:

```bash
python3 -c "import json; d=json.load(open('$(ls -t tools/report/control_plane/observer/*.json | head -1)')); print(d['signals'].get('coverage_signal', {}))"
```

Coverage collection requires pre-existing report files. ControlPlane never runs coverage tools itself. Generate coverage reports as part of your CI or test script, then retain the output files.

## Theme Aggregation

When the same source file appears in top lint or type violations across 3+ consecutive observer snapshots, `ThemeAggregationDeriver` emits `theme/lint_cluster` or `theme/type_cluster` insights. The `LintClusterRule` proposes a single `[Refactor]` task for that file rather than repeated `lint_fix` proposals.

If you see `[Refactor] Systematic lint cleanup: <file>` tasks being proposed, it means the file has persistent violations that individual lint fixes are not resolving. The refactor task asks Kodo to address the root pattern rather than individual violations.

## Rejection Patterns Store

When a PR is escalated to human review and the human reviewer leaves comments, rejection patterns are automatically extracted and stored:

```bash
cat state/rejection_patterns.json
```

Each key is `{repo_key}:{family}`. Each entry has `patterns` (pattern name → count) and `last_seen` (pattern name → ISO timestamp). The most frequently seen patterns are the main recurring feedback from human reviewers for that family in that repo.

These patterns are currently persisted for observability. Future work can wire them into proposal descriptions to pre-empt known objections.

## Execution Environment Warnings

When a goal task is claimed for a family that requires specific tools, the watcher checks tool availability:

```bash
grep "execution_env_warning" logs/local/watch-all/goal.log
```

Warning fields: `task_id`, `family`, `warning` (describes which tool group is missing). The task is not blocked — it proceeds to execution — but repeated warnings for the same family indicate the tool should be installed in the repo's venv or system PATH.

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
