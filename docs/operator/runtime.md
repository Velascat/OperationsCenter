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

## Logs And Artifacts

- command logs: `logs/local/`
- Plane runtime logs: `logs/local/plane-runtime/`
- watcher logs, PIDs, heartbeat files: `logs/local/watch-all/`
- retained run artifacts: `tools/report/kodo_plane/`
- observer snapshots: `tools/report/control_plane/observer/`
- insight artifacts: `tools/report/control_plane/insights/`
- decision artifacts: `tools/report/control_plane/decision/`
- proposer result artifacts: `tools/report/control_plane/proposer/`

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
