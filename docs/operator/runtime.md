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
./scripts/control-plane.sh watch-all
./scripts/control-plane.sh watch-all-status
./scripts/control-plane.sh watch-all-stop
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

### `watch --role review`

- polls open PRs tracked in `state/pr_reviews/` every 60 seconds (configurable via `CONTROL_PLANE_WATCH_INTERVAL_REVIEW_SECONDS`)
- only active for repos with `await_review: true` in config
- drives the two-phase review loop:
  - **self-review phase**: kodo evaluates its own diff and either merges (LGTM) or revises and retries
  - **human review phase**: responds to human comments with kodo revision passes; merges on 👍 or 1-day timeout
- ignores comments from accounts listed in `reviewer.bot_logins` and comments carrying the `<!-- controlplane:bot -->` marker
- on startup, backfills state files for any open PRs that pre-date the watcher

### `backfill-pr-reviews`

Scans GitHub for open PRs on all `await_review`-enabled repos and creates missing state files. Run this after a watcher restart to recover any PRs that were opened before the watcher started.

### `watch-all`

- local convenience wrapper that launches all five lanes together: `goal`, `test`, `improve`, `propose`, `review`
- writes separate logs and PID files
- not a scheduler cluster or distributed supervisor

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

The wrapper also runs janitor automatically before commands.

Default retention is 1 day and can be changed with:

```bash
CONTROL_PLANE_RETENTION_DAYS=<days>
```

## Runtime Boundaries

Current runtime is:

- local-first
- polling-based
- single-machine
- one watcher process per role

It is not:

- a queue
- a distributed scheduler
- a multi-host supervisor
- an unlimited autonomous planner
