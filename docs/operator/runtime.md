# Runtime Guide

This repo runs as a local polling workflow.

## Main Commands

```bash
./scripts/control-plane.sh start
./scripts/control-plane.sh stop
./scripts/control-plane.sh run --task-id TASK-123
./scripts/control-plane.sh run-next
./scripts/control-plane.sh watch --role goal
./scripts/control-plane.sh watch --role test
./scripts/control-plane.sh watch --role improve
./scripts/control-plane.sh watch --role propose
./scripts/control-plane.sh watch --role review
./scripts/control-plane.sh watch --role spec
./scripts/control-plane.sh watch-all
./scripts/control-plane.sh watch-all-status
./scripts/control-plane.sh watch-all-stop
./scripts/control-plane.sh watch-stop --role goal
./scripts/control-plane.sh backfill-pr-reviews
./scripts/control-plane.sh observe-repo
./scripts/control-plane.sh generate-insights
./scripts/control-plane.sh decide-proposals
./scripts/control-plane.sh propose-from-candidates
./scripts/control-plane.sh autonomy-cycle
./scripts/control-plane.sh tune-autonomy
./scripts/control-plane.sh autonomy-tiers
```

## Watchers

### `watch --role goal`

- polls for `task-kind: goal`
- claims work by moving it to `Running`
- runs implementation work

### `watch --role test`

- polls for `task-kind: test`
- runs verification work

### `watch --role improve`

- polls for explicit `task-kind: improve`
- also inspects `Blocked` tasks for triage

### `watch --role propose`

- runs a bounded proposal cycle when the board is sufficiently idle or recent signals justify it
- creates Plane tasks instead of executing open-ended repo changes
- uses cooldowns, quotas, and deduplication to avoid board spam
- prefers `Ready for AI` only for strong, bounded tasks; otherwise uses `Backlog`
- skips the proposal cycle when the `Ready for AI` backlog already has ≥ `propose_skip_when_ready_count` tasks (default 8) — look for `watch_propose_skipped_backlog` in the propose watcher log

### `watch --role review`

- polls open PRs tracked in `state/pr_reviews/` every 60 seconds (configurable via `CONTROL_PLANE_WATCH_INTERVAL_REVIEW_SECONDS`)
- only active for repos with `await_review: true` in config
- drives the two-phase review loop:
  - **self-review phase**: kodo evaluates its own diff and either merges (LGTM) or revises and retries
  - **human review phase**: responds to human comments with kodo revision passes; merges on 👍 or 1-day timeout
- ignores comments from accounts listed in `reviewer.bot_logins` and comments carrying the `<!-- controlplane:bot -->` marker
- on startup, backfills state files for any open PRs that pre-date the watcher
- auto-merges autonomy PRs when every failing CI check matches a `ci_ignored_checks` pattern (pre-existing failures, not caused by the PR)
- auto-resolves pip dependency conflicts in `requirements*.txt` / `pyproject.toml` without human review

### `watch --role spec`

- polls for spec campaign trigger conditions (drop-file, Plane label, queue drain)
- when triggered: brainstorms a spec via Claude, creates a Plane campaign with child tasks
- runs stall detection and self-recovery on every cycle
- suppresses heuristic proposals for campaign area while campaign is active
- controlled by `spec_director:` config block

### `watch-stop`

Stop a single watcher role without stopping all six:

```bash
./scripts/control-plane.sh watch-stop --role goal
./scripts/control-plane.sh watch-stop --role test
./scripts/control-plane.sh watch-stop --role propose
```

Sends SIGTERM to the PID recorded in `logs/local/watch-all/<role>.pid`. The restart loop exits cleanly; the python worker finishes its current cycle or is interrupted by the signal. Use `watch-all-stop` to stop all roles at once.

### `backfill-pr-reviews`

Scans GitHub for open PRs on all `await_review`-enabled repos and creates missing state files. Run this after a watcher restart to recover any PRs that were opened before the watcher started.

### `watch-all`

- local convenience wrapper that launches all six lanes together: `goal`, `test`, `improve`, `propose`, `review`, `spec`
- writes separate logs and PID files
- not a scheduler cluster or distributed supervisor

Each watcher lane runs inside a bash restart loop. If the python process exits with a non-zero code (crash, OOM, unhandled exception), the loop logs a `watcher_restart` event and relaunches after 5 seconds. An exit code of 0 (intentional stop — e.g. credential failure) breaks the loop. `watch-all-stop` and `watch-stop` send SIGTERM, which the trap handler catches before killing the python child cleanly.

### Startup Cleanup (Cycle 1)

On the first cycle of each run, watchers perform two cleanup passes before polling for work:

1. **Stale running task reconciliation** — tasks that have been in `Running` state for more than 15 minutes are re-queued to `Ready for AI`. This is a shorter TTL than the normal 120-minute running timeout and is intentional: if the system just restarted, tasks that were running against now-dead workers should be re-claimed quickly. The short TTL only applies on cycle 1.

2. **Orphaned workspace cleanup** — `/tmp/cp-task-*` directories not referenced by any live process are deleted. These accumulate when kodo workers are killed mid-run (OOM, SIGKILL, power cycle). Deleted paths are logged as `watch_cleanup_orphaned_workspaces`.

Periodic orphan cleanup also runs automatically every 20 cycles for goal, test, and improve watchers — no operator action required.

## Heartbeat And Status

`watch-all-status` reports:

- whether each watcher is running
- cycle number
- last action
- current task id/task kind when present
- follow-up counts for the current session
- blocked tasks triaged for the current session
- autonomous tasks proposed for the current session
- last update time

This is the quickest way to tell whether the local system is alive or stalled.

Each watcher also writes a `heartbeat_<role>.json` file to `logs/local/watch-all/` at the start of every cycle. Use the heartbeat-check CLI to verify all watchers are alive:

```bash
python -m control_plane.entrypoints.worker.main heartbeat-check --log-dir logs/local/watch-all
```

Exits with code 0 when all watchers are healthy, code 1 when any heartbeat is stale (> 5 minutes old). Suitable for cron-based monitoring.

## Parallel Execution

Each watcher lane runs one task at a time by default. For higher throughput, set `parallel_slots` in config or pass `--parallel-slots N` on the CLI:

```bash
./scripts/control-plane.sh watch --role goal --parallel-slots 3
```

Or in config:
```yaml
parallel_slots: 2
```

Slot 0 is the primary slot and runs all periodic scans (heartbeat, improve sub-scans, config drift check, credential validation). Additional slots only execute tasks. The Plane API's state machine prevents two slots from claiming the same task.

**Always review board throughput before increasing slots** — parallel execution amplifies any misconfiguration in task scope or execution budget.

## Resource Throttling

The goal and test watchers self-throttle before launching Kodo to avoid overloading the machine.

### Kodo Concurrency Gate

Before each execution the watcher counts live `kodo` processes in `/proc/*/cmdline`. If the count equals or exceeds `max_concurrent_kodo`, the cycle is skipped (housekeeping still runs). The default is 1 — only one Kodo instance at a time on the machine.

```yaml
max_concurrent_kodo: 2   # allow two parallel kodo runs (e.g. 2 repos)
```

Set to 0 to disable the check.

Look for `watch_skip_kodo_gate` with `"reason": "kodo_concurrency_cap"` in the watcher log when the gate fires.

### Memory Gate

Before each execution the watcher reads `MemAvailable` from `/proc/meminfo`. If available memory is below `min_kodo_available_mb`, the cycle is skipped. The default is 400 MB.

```yaml
min_kodo_available_mb: 600   # require at least 600 MB free before launching kodo
```

Set to 0 to disable the check.

Look for `watch_skip_kodo_gate` with `"reason": "low_memory"` when the gate fires.

### Propose Backlog Gate

The propose watcher skips proposal generation when the `Ready for AI` queue is already full. The default threshold is 8 tasks.

```yaml
propose_skip_when_ready_count: 12   # allow up to 12 ready tasks before pausing proposals
```

Set to 0 to disable. Look for `watch_propose_skipped_backlog` in the propose watcher log.

## Spend Report

To see how many tasks have been executed and their estimated cost:

```bash
# Last 24 hours (default)
python -m control_plane.entrypoints.worker.main spend-report

# Last 7 days
python -m control_plane.entrypoints.worker.main spend-report --window-days 7
```

Cost tracking requires `cost_per_execution_usd` to be set in config (default 0.0 = disabled). The value is operator-supplied; ControlPlane does not parse Kodo billing output.

```yaml
cost_per_execution_usd: 0.15   # rough estimate per task run
```

## Logs And Artifacts

- command logs: `logs/local/`
- Plane runtime logs: `logs/local/plane-runtime/`
- watcher logs, PIDs, heartbeat files: `logs/local/watch-all/`
- retained run artifacts: `tools/report/kodo_plane/`
- observer snapshots: `tools/report/control_plane/observer/`
- insight artifacts: `tools/report/control_plane/insights/`
- decision artifacts: `tools/report/control_plane/decision/`
- proposer result artifacts: `tools/report/control_plane/proposer/`

### Machine-Level Kodo Artifacts (`~/.kodo/`)

Kodo maintains its own run history at `~/.kodo/runs/` — one JSON file per execution. This is a machine-level bookkeeping directory written by Kodo itself, not by ControlPlane. It accumulates over time (hundreds of entries, tens of MB) and is safe to leave in place.

The `~/.kodo/` directory is entirely separate from any per-repo `.kodo/` runtime directory that Kodo may create inside an ephemeral workspace during a run (e.g. `.kodo/team.json` for the Claude fallback override). The per-repo `.kodo/` directory is cleaned up automatically after each task run.

If disk space is a concern, prune old Kodo run history manually:

```bash
ls -lt ~/.kodo/runs/ | wc -l            # count entries
ls -lt ~/.kodo/runs/ | tail -n +100 | awk '{print $NF}' | xargs -I{} rm ~/.kodo/runs/{}  # keep newest 100
```

## Board Saturation Backpressure

The propose watcher and `autonomy-cycle` both enforce a board saturation limit to prevent flooding the queue with unexecuted autonomy tasks. Before creating any new proposals, the system counts open tasks labeled `source: autonomy` in `Ready for AI` and `Backlog`. If the count meets or exceeds the limit, the propose stage is skipped for that cycle.

Default limit is 15. Configurable via environment variable:

```bash
CONTROL_PLANE_MAX_QUEUED_AUTONOMY_TASKS=20 ./scripts/control-plane.sh watch --role propose
```

When saturated, look for `"event": "propose_skipped_board_saturated"` in the propose watcher log. This is not an error — it means the board already has more work than the workers are consuming.

## Semantic Deduplication

In addition to exact-title matching, proposals are checked for near-duplicate titles using Jaccard similarity on word tokens (minimum 3 characters). Titles with similarity ≥ 0.5 are considered duplicates and suppressed. `[...]` prefix markers (e.g. `[Regression]`, `[Lint]`) are stripped before comparison so prefixed variants of the same underlying proposal are caught.

If a legitimate proposal is being suppressed as a near-duplicate, check the board for existing tasks with similar titles. The threshold is not configurable at runtime.

## Task Urgency Scoring

When the watcher selects which task to pick up next, candidates are ranked by a composite urgency score rather than priority label alone:

| Component | Weight |
|-----------|--------|
| Priority label (`urgent`=4, `high`=3, `medium`=2, `low`=1) | base |
| Title prefix boost (`[Regression]`=+3, `[Systemic]`=+2, `[Workspace]`=+1) | additive |
| Task age (days since creation) | additive |

This ensures regression and systemic investigation tasks are processed before routine backlog, even when they share the same priority label.

## Disk Space Guardrail

Before writing to the usage store, a disk space check runs against the storage path:

- **below 50 MB free**: raises `OSError` — the usage store write is blocked and the event is logged
- **below 200 MB free**: logs a `disk_space_low` warning but continues

The check also runs in `autonomy-cycle` before writing the cycle report. If you see `OSError: insufficient disk space`, free space on the device hosting `tools/report/`.

## Autonomy-Cycle

The preferred way to run the full autonomy pipeline in one command:

```bash
# Dry-run (default) — shows what would be proposed, no Plane writes
./scripts/control-plane.sh autonomy-cycle --config <config.yaml>

# Execute — creates real Plane tasks
./scripts/control-plane.sh autonomy-cycle --config <config.yaml> --execute

# Execute with all candidate families enabled
./scripts/control-plane.sh autonomy-cycle --config <config.yaml> --execute --all-families
```

**Always review the dry-run output before adding `--execute`**, especially after:
- changing threshold config or tuning heuristics
- restarting watchers after a budget-exhaustion event
- promoting a new candidate family from gated to active

The dry-run output shows which families fired, which candidates would be emitted vs. suppressed, and suppression reasons. Every run writes a structured report to `logs/autonomy_cycle/cycle_<ts>.json`.

If the proposer has emitted 0 candidates for 5 consecutive cycles, a `logs/autonomy_cycle/quiet_diagnosis.json` file is written automatically. It aggregates suppression reasons across those cycles (counted and sorted by frequency) with a human-readable `advice` field. The file is deleted when the proposer starts emitting again. This removes the need to manually diff multiple cycle JSON files to diagnose silence.

## Tune-Autonomy

The bounded self-tuning regulation loop. Run this as a periodic maintenance step, not on every cycle:

```bash
# Recommendation-only (default, safe — no config changes)
./scripts/control-plane.sh tune-autonomy

# With wider artifact window
./scripts/control-plane.sh tune-autonomy --window 30

# Auto-apply mode (opt-in, requires env var as second gate)
CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1 ./scripts/control-plane.sh tune-autonomy --apply
```

Reads retained decision, proposer, and feedback artifacts. Produces per-family metrics including acceptance rates and emits conservative recommendations. See `docs/operator/tuning.md` and `docs/design/autonomy_self_tuning_regulator.md` for full details.

## Autonomy-Tiers

Manages per-family autonomy tiers that control the initial Plane task state:

```bash
# Show current tier configuration
./scripts/control-plane.sh autonomy-tiers show

# Promote a family to auto-execute (tier 2)
./scripts/control-plane.sh autonomy-tiers set --family lint_fix --tier 2

# Demote a family to backlog-only (tier 1)
./scripts/control-plane.sh autonomy-tiers set --family type_fix --tier 1

# Disable auto-creation for a family (tier 0)
./scripts/control-plane.sh autonomy-tiers set --family arch_promotion --tier 0
```

Tier changes are written to `config/autonomy_tiers.json` and take effect on the next `autonomy-cycle` run.

## Promote-Backlog

When a family's tier is raised from 1 to 2, tasks already sitting in Backlog from before the tier change will not move automatically — they were created before the config update. `promote-backlog` finds those tasks and moves them to `Ready for AI`.

```bash
# Dry-run (default): show what would be promoted, no Plane writes
./scripts/control-plane.sh promote-backlog

# Promote all tier-2 families
./scripts/control-plane.sh promote-backlog --execute

# Promote one family only
./scripts/control-plane.sh promote-backlog --family lint_fix --execute
```

Promotion criteria (all must be true):
- Task is in `Backlog` state.
- Task has label `source: autonomy`.
- Task body contains `source_family: <family>` in the `## Provenance` block.
- Current effective tier for that family (from `config/autonomy_tiers.json`) is >= 2.

Tasks created at tier 0 are never promoted. Tasks without a `source: autonomy` label are not touched.

## Error Ingestion Service

The error ingest service bridges production runtime errors into Plane tasks automatically. It supports two input modes:

**Webhook receiver** — listens for HTTP `POST /ingest` with JSON body `{text, repo_key?}`:

```bash
python -m control_plane.entrypoints.error_ingest.main \
    --config config/control_plane.local.yaml
```

Configure in `config/control_plane.local.yaml`:
```yaml
error_ingest:
  webhook_port: 9000
  default_repo_key: myrepo
  log_sources:
    - path: /var/log/myapp/app.log
      repo_key: myrepo
      pattern: "(ERROR|CRITICAL)"
      dedup_window_seconds: 3600
```

**Log file tailing** — follows one or more log files for lines matching a regex; each match creates a Plane task.

Duplicate suppression is handled via `state/error_ingest_dedup.json`. The dedup key is a stable hash of `(repo_key, text[:200])`; within `dedup_window_seconds`, the same error text will not create a second task. Delete the file to reset dedup state.

## Explicit Approval Control

By default, the reviewer watcher merges autonomy PRs after a 1-day timeout with no unresolved comments. For repos that must not auto-merge without a human sign-off, set:

```yaml
repos:
  production_repo:
    require_explicit_approval: true
```

When enabled:
- Timeout-based merges are blocked for that repo.
- A reminder comment is posted on the PR asking for explicit approval (at most once per day).
- The PR stays open until a human approves via 👍 or an explicit `LGTM` comment.

This does not affect the `auto_merge_on_ci_green` path, which is controlled separately. Both flags can coexist; `require_explicit_approval` blocks only the timeout path.

## Feedback

Records proposal outcomes manually for tasks that were merged, escalated, or abandoned outside the reviewer loop:

```bash
# Record a merge
python -m control_plane.entrypoints.feedback.main record \
    --task-id <uuid> --outcome merged --pr-number 42

# Record an escalation
python -m control_plane.entrypoints.feedback.main record \
    --task-id <uuid> --outcome escalated

# Record abandonment
python -m control_plane.entrypoints.feedback.main record \
    --task-id <uuid> --outcome abandoned

# Record with confidence calibration data (optional)
python -m control_plane.entrypoints.feedback.main record \
    --task-id <uuid> --outcome merged --family lint_fix --confidence high

# List all feedback records
python -m control_plane.entrypoints.feedback.main list

# Show feedback for a specific task
python -m control_plane.entrypoints.feedback.main show --task-id <uuid>
```

The reviewer watcher writes feedback records automatically when it merges or escalates a PR. Use this command for manual retroactive recording.

## Repo-Aware Autonomy Stages

The repo-aware autonomy chain is explicit:

```text
observe-repo -> generate-insights -> decide-proposals -> propose-from-candidates
```

Each stage writes its own retained artifact before the next stage consumes it.

### Dry-Run-First Posture

**Always run `autonomy-cycle` dry-run before executing.** This is the default behavior.

```bash
# Dry-run (default) — shows what would be proposed, no Plane writes
./scripts/control-plane.sh autonomy-cycle

# Execute — creates real Plane tasks
./scripts/control-plane.sh autonomy-cycle --execute

# Execute with all candidate families enabled
./scripts/control-plane.sh autonomy-cycle --execute --all-families
```

The dry-run output shows:
- which candidate families fired
- which candidates would be emitted vs. suppressed
- suppression reasons (cooldown, quota, budget, family-gating)

Review this output before adding `--execute`, especially after:
- changing threshold config or tuning heuristics
- restarting watchers after a budget-exhaustion event
- promoting a new candidate family from gated to active

The stage-by-stage commands also support dry-run:
```bash
./scripts/control-plane.sh decide-proposals --dry-run
./scripts/control-plane.sh propose-from-candidates --dry-run
```

Use these when you want to inspect a single stage without running the full chain.

## Retention

```bash
./scripts/control-plane.sh janitor
```

The wrapper runs janitor automatically before write commands. Read-only and status commands (`watch-all-status`, `dev-status`, `watch-all-stop`, `watch-stop`, `plane-status`, `providers-status`, `doctor`) skip the janitor to avoid unnecessary work.

Default retention is 1 day and can be changed with:

```bash
CONTROL_PLANE_RETENTION_DAYS=<days>
```

## Process Supervisor

For unattended operation, use the process supervisor instead of (or on top of) the bash restart loops in `watch-all`. The supervisor is manifest-driven, tracks restart counts, and monitors heartbeat files independently of the shell session:

```bash
python -m control_plane.entrypoints.supervisor.main \
    --manifest config/supervisor_manifest.yaml \
    --log-dir logs/local \
    --check-interval 30
```

Example manifest (`config/supervisor_manifest.yaml`):
```yaml
processes:
  - role: goal
    command: ["python", "-m", "control_plane.entrypoints.worker.main",
              "--config", "config/control_plane.local.yaml",
              "--watch", "--role", "goal", "--status-dir", "logs/local"]
    restart_backoff_seconds: 10
  - role: improve
    command: [...]
    restart_max: 20        # stop trying after 20 crashes
  - role: reviewer
    command: [...]
```

The supervisor writes `logs/local/supervisor.status.json` on every check — readable by `watch-all-status` or any external monitor. It restarts a process when: (a) the process exits, or (b) its heartbeat file is >5 minutes old (the process is alive but frozen).

## Pipeline Trigger (Event-Driven)

For reactive operation instead of scheduled-only runs, use the pipeline trigger daemon:

```bash
python -m control_plane.entrypoints.pipeline_trigger.main \
    --config config/control_plane.local.yaml \
    --execute \
    --min-interval 300    # minimum seconds between runs (default 300)
    --poll-interval 30    # how often to check trigger files (default 30)
```

The trigger watches three sources:
- `.git/FETCH_HEAD` in each repo's `local_path` — fires when a new fetch/push arrives
- `state/error_ingest_dedup.json` — fires when a runtime error is ingested
- `tools/report/kodo_plane/` child count — fires when new execution artifacts appear

Trigger state is persisted in `state/pipeline_trigger_state.json`. The debounce (`--min-interval`) prevents re-running on every small change; 5 minutes is a reasonable default for most repos.

**When to use:** Pair with the watcher processes (`watch-all`) for fully reactive operation. The trigger handles pipeline refresh; the watchers handle task execution.

## Dependency Update Loop

The improve watcher automatically scans for outdated pip packages every 50 cycles for repos with a `local_path` configured. Major-version bumps trigger bounded Plane tasks in Backlog (at most 2 per scan). No operator action is needed.

To surface dependency updates immediately without waiting for a cycle:

```bash
# Run the improve watcher manually for one scan cycle
python -m control_plane.entrypoints.worker.main \
    --config config/control_plane.local.yaml --first-ready --role improve
```

## Maintenance Windows

To pause all autonomous execution during planned maintenance (deploy windows, overnight freezes):

```yaml
maintenance_windows:
  - start_hour: 2        # UTC
    end_hour: 4
    days: [0, 1, 2, 3, 4]   # Mon–Fri only; empty = every day
```

While a window is active, watchers log `watch_maintenance_window` and sleep the full poll interval without polling. The autonomy-cycle pipeline also skips proposal creation during windows.

## Coverage Signal Collection

The observer collects test coverage data automatically when coverage reports are present in the repo or logs directory. No configuration is needed — coverage collection is a safe no-op when reports are absent.

Supported report formats (checked in priority order):
1. `coverage.xml` — Cobertura XML produced by `coverage xml` or `pytest --cov`
2. `pytest-coverage.txt` / `coverage.txt` — text totals only
3. `htmlcov/index.html` — HTML report from `coverage html`

To enable coverage proposals, generate coverage reports as part of your normal test run and keep the output files in the repo root or your logs directory. ControlPlane never runs coverage tools itself.

When coverage data is available, the autonomy pipeline can propose:
- `[Improve] Improve test coverage (currently N%)` — when total coverage falls below 60%
- `[Improve] Add tests for N under-covered file(s)` — when ≥3 files are below 80%

## Cross-Repo Impact Paths

When a task modifies shared interface paths, a warning comment is posted on the task automatically. Declare shared paths per-repo:

```yaml
repos:
  shared_lib:
    impact_report_paths:
      - src/api/         # any change under src/api/ triggers a warning
      - proto/
```

Look for `[Goal] Cross-repo impact detected` comments on tasks. This does not block the task — it is an advisory that downstream repos may need to be checked.

## Audit Log Export

Export a structured execution audit trail for the last N days:

```bash
# Last 7 days (default)
python -m control_plane.entrypoints.worker.main audit-export

# Last 30 days, save to file
python -m control_plane.entrypoints.worker.main audit-export --window-days 30 > audit.json
```

Each entry has `kind: "execution"`, `task_id`, `outcome`, `succeeded`, `role`, `kodo_version`, and `timestamp`. Use for compliance review or debugging failure patterns.

## Board Health Snapshot

```bash
python -m control_plane.entrypoints.worker.main board-health \
    --config config/control_plane.local.yaml
```

Returns a JSON list of board anomalies:
- `stuck_running` — ≥3 tasks in Running state simultaneously
- `clustered_blocked_reason` — ≥5 blocked tasks with the same classification
- `quiet_repo_lane` — a configured repo has zero active tasks

Also runs automatically every 40 improve cycles with anomalies logged as `board_health_anomalies` warnings.

## Per-Repo Daily Execution Cap

Prevent one high-volume repo from consuming the full daily execution budget:

```yaml
repos:
  high_volume_repo:
    max_daily_executions: 5   # default: no per-repo limit
```

When the cap is reached, the watcher logs `skip_repo_budget` and moves to the next task. The global budget still applies independently.

## Session 10 Features (S10)

### Campaign Status CLI

Track multi-step execution plan progress across related tasks:

```bash
python -m control_plane.entrypoints.campaign_status.main
python -m control_plane.entrypoints.campaign_status.main --status in_progress
python -m control_plane.entrypoints.campaign_status.main --json
```

Campaigns are registered automatically when `build_multi_step_plan()` decomposes a complex task. Records are stored in `state/campaigns.json`.

### CI Webhook Receiver

Receive real-time GitHub `check_run` events instead of polling:

```bash
python -m control_plane.entrypoints.ci_webhook.main --host 127.0.0.1 --port 8765
```

Environment variables:
- `CONTROL_PLANE_WEBHOOK_SECRET` — HMAC secret from GitHub webhook settings (required for signature validation)
- `CONTROL_PLANE_WEBHOOK_PORT` — port (default: 8765)
- `CONTROL_PLANE_WEBHOOK_HOST` — host (default: 127.0.0.1)
- `CONTROL_PLANE_WEBHOOK_TRIGGER` — optional command to run on CI event (default: write trigger file to `state/ci_webhook_triggers/`)

Configure GitHub to send `check_run` events to `http://your-host:8765/webhook`.

### Awaiting Input — Mid-Execution Questions

Kodo can signal it needs clarification by including `<!-- cp:question: <text> -->` in its output. The improve watcher detects this, marks the task Blocked with `awaiting_input` classification, and posts the question for the operator.

After the operator replies in the task comments, `handle_awaiting_input_scan()` (every 8 improve cycles) detects the answer, injects it into the task description, and re-queues the task to `Ready for AI`.

### Calibration Cleanup

Remove stale calibration events manually:

```python
from control_plane.tuning.calibration import ConfidenceCalibrationStore
removed = ConfidenceCalibrationStore().cleanup_old_events(window_days=90)
print(f"Removed {removed} old events")
```

This is also safe to call periodically in automation; `calibration_for()` and `report()` already apply a 90-day window by default.

## Runtime Boundaries

Current runtime is:

- local-first
- polling-based
- single-machine
- one watcher process per role (plus optional supervisor)

It is not:

- a queue
- a distributed scheduler
- a multi-host supervisor
- an unlimited autonomous planner
