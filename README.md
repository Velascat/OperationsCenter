# Control Plane

Local autonomous coding workflow system that uses **Plane** as the board, **Control Plane** as the worker wrapper, and **Kodo** as the single-run coding engine.

## What This System Is

- **Plane** is the board and source of truth for tasks, states, comments, and labels.
- **Control Plane** is the local autonomous wrapper that watches the board, prepares isolated workspaces, runs tasks, and writes results back.
- **Kodo** is the execution engine used inside a single task run.
- **goal**, **test**, **improve**, and **propose** are the board-facing worker lanes.
- The system is **local-first**, **single-machine**, and **polling-based** today.

## System Boundaries

- Local-first and single-machine today.
- Plane remains the board and source of truth for work state.
- Repo-aware autonomy is artifact-driven and bounded by guardrails.
- The proposer lane remains the board-facing guarded adapter.
- Expensive execution is budget-aware and suppresses unnecessary runs.
- Repo-aware autonomy stages remain explicit and inspectable for now.

## Repo-Aware Autonomy Loop

```text
observe -> analyze -> decide -> propose
```

- `observe` writes retained repo-state snapshots.
- `analyze` derives normalized insights from those snapshots.
- `decide` emits guarded proposal candidates plus suppression records.
- `propose` routes approved candidates into the existing board-facing proposer lane, which applies final protections before creating bounded Plane tasks.

## Repo-Aware Autonomy Flow

```text
[Repo]
  ↓
[Observer] -> repo_state_snapshot.json
  ↓
[Insight Engine] -> repo_insights.json
  ↓
[Decision Engine] -> proposal_candidates.json
  ↓
[Board-Facing Proposer Lane] -> proposal_results.json -> Plane tasks
```

## Autonomy Execution Model (Current)

The repo-aware autonomy loop is currently stage-driven and explicit.

- `observe-repo` writes a retained repo-state snapshot.
- `generate-insights` reads retained snapshots and emits normalized insights.
- `decide-proposals` reads retained insights and emits guarded proposal candidates.
- `propose-from-candidates` routes emitted candidates through existing proposer protections and may create bounded Plane tasks.

Today, these stages are run explicitly rather than through an automatic end-to-end wrapper. That is intentional: it keeps the autonomy loop inspectable, easy to validate, and easy to tune before a future chained wrapper exists.

## `Propose` Means Two Related Things

- In the repo-aware autonomy loop, `propose` is the stage that routes approved candidates toward task creation.
- In the worker model, the proposer lane is the board-facing path that applies final guardrails and writes Plane tasks.

The naming is intentionally close because the second is the board adapter for the first, but they are not the same boundary.

## What Works Today

### Core Workflow

- Single-task execution from a Plane work item.
- Background watchers for `goal`, `test`, `improve`, and `propose`.
- `watch-all` to launch the four local watcher lanes together.
- Structured task parsing from `## Execution`, `## Goal`, and optional `## Constraints`.
- Isolated ephemeral clone + task branch workflow.
- Repo-local bootstrap and validation execution.
- Worker comments, retained artifacts, and local heartbeat/status files.

### Repo-Aware Autonomy

- Read-only repo observer snapshots.
- Read-only normalized insight generation from retained observer snapshots.
- Guarded proposal-candidate generation from retained insights.
- Candidate-driven Plane task creation through the proposer lane with provenance and dedup protection.
- End-to-end coverage of the repo-aware autonomy handoff chain from observer -> insights -> decision -> proposer.
- Improve-worker blocked-task triage, repeated-failure pattern detection, and bounded follow-up task creation.
- Proposer idle-board task generation with cooldowns, quotas, and deduplication.
- Dependency drift reporting with optional Plane improve-task creation.

### Execution Safety

- Execution budget enforcement, retry caps, no-op suppression, and proposal suppression when execution budget is low.

### Operator UI and Discovery

- Repo-control UI for repo discovery, repo import, branch selection, and proposer scope control.
- GitHub-backed repo and branch discovery for configured owners, with private-repo support when `GITHUB_TOKEN` is set.
- Live board polling view that reflects Plane work-item changes without manually refreshing the Plane issues page.

## Lifecycle Contract

```text
goal -> review
goal -> test -> done
goal -> blocked -> improve -> follow-up goal/test or human attention
test -> goal when verification fails
improve -> bounded follow-up work, not open-ended rewrites
propose -> bounded board tasks when the board is idle or recent signals justify it
```

## Fastest Happy Path

```bash
./scripts/control-plane.sh setup
source .env.control-plane.local
./scripts/control-plane.sh dev-up
./scripts/control-plane.sh dev-status
```

Then:

1. Create a Plane work item in the configured project.
2. Move it to `Ready for AI`.
3. Watch the local loop with:

```bash
./scripts/control-plane.sh dev-status
```

## Core Commands

```bash
./scripts/control-plane.sh setup
./scripts/control-plane.sh start
./scripts/control-plane.sh stop
./scripts/control-plane.sh plane-status
./scripts/control-plane.sh api-up
./scripts/control-plane.sh api-down
./scripts/control-plane.sh api-status
./scripts/control-plane.sh run --task-id TASK-123
./scripts/control-plane.sh run-next
./scripts/control-plane.sh watch --role goal
./scripts/control-plane.sh watch --role test
./scripts/control-plane.sh watch --role improve
./scripts/control-plane.sh watch --role propose
./scripts/control-plane.sh watch-all
./scripts/control-plane.sh watch-all-status
./scripts/control-plane.sh watch-all-stop
./scripts/control-plane.sh dev-up
./scripts/control-plane.sh dev-down
./scripts/control-plane.sh dev-status
./scripts/control-plane.sh observe-repo
./scripts/control-plane.sh generate-insights
./scripts/control-plane.sh decide-proposals
./scripts/control-plane.sh decide-proposals --dry-run
./scripts/control-plane.sh propose-from-candidates
./scripts/control-plane.sh propose-from-candidates --dry-run
./scripts/control-plane.sh plane-doctor --task-id TASK-123
./scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
./scripts/control-plane.sh dependency-check
./scripts/control-plane.sh janitor
./scripts/control-plane.sh api
```

## Execution Controls

Control Plane now enforces a bounded execution-control layer before expensive worker actions run.

- rolling execution budgets over hourly and daily windows
- retry caps per task for automatic `goal` and `test` execution
- watcher-side no-op suppression for unchanged task signatures
- proposal suppression when execution budget is low
- explicit retained accounting in `tools/report/control_plane/execution/usage.json`

Default local knobs are set in [.env.control-plane.local](/home/dev/Documents/GitHub/ControlPlane/.env.control-plane.local):

- `CONTROL_PLANE_MAX_EXEC_PER_HOUR`
- `CONTROL_PLANE_MAX_EXEC_PER_DAY`
- `CONTROL_PLANE_MAX_RETRIES_PER_TASK`
- `CONTROL_PLANE_MIN_REMAINING_EXEC_FOR_PROPOSALS`
- `CONTROL_PLANE_WATCH_INTERVAL_GOAL_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_TEST_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_IMPROVE_SECONDS`
- `CONTROL_PLANE_WATCH_INTERVAL_PROPOSE_SECONDS`

Skipping is treated as a valid outcome when execution is not justified. Budget skips, no-op skips, retry-cap blocks, and proposal suppression are written to retained artifacts and surfaced in watcher logs.

## Repo Control UI

Control Plane now exposes a small local UI and API for repo operations:

- Repo control page: `http://127.0.0.1:8787/`
- Live board: available in the same UI, with a polling work-item table
- Repo list source: GitHub discovery for the owner(s) inferred from configured repos
- Branch list source: GitHub branch API for each discovered/configured repo
- Propose watcher scope: controlled by per-repo `propose_enabled` policy
- Repo import: discovered repos can be imported into local Control Plane config directly from the UI

Notes:

- The watcher only operates on repos that are actually configured in `config/control_plane.local.yaml`.
- Discovered repos are visible before import, but remain read-only/unconfigured until imported.
- Private repos require a valid `GITHUB_TOKEN` in `.env.control-plane.local`.

## Local Stack Startup

The easiest way to bring up the whole local system is:

```bash
./scripts/control-plane.sh dev-up
```

That starts:

- Plane on `http://localhost:8080`
- the Control Plane UI/API on `http://127.0.0.1:8787/`
- all four watcher lanes: `goal`, `test`, `improve`, and `propose`

Useful companions:

- `./scripts/control-plane.sh dev-status`
- `./scripts/control-plane.sh dev-down`
- `./scripts/control-plane.sh api-up`
- `./scripts/control-plane.sh api-down`
- `./scripts/control-plane.sh api-status`

## Live Board

The self-hosted Plane issues page does not reliably auto-refresh in this local setup, even though the Plane API reflects updates immediately.

Use the Control Plane UI at `http://127.0.0.1:8787/` when you want a live-updating task view:

- `Live Board` polls Plane every 5 seconds
- shows work-item ids, titles, states, and update timestamps
- useful for watching watcher/proposer activity without manually refreshing the Plane frontend

## Runtime Files

- Command logs: `logs/local/`
- Plane runtime logs: `logs/local/plane-runtime/`
- Watcher logs, PIDs, and heartbeat files: `logs/local/watch-all/`
- Retained execution artifacts: `tools/report/kodo_plane/`
- Retained execution usage ledger: `tools/report/control_plane/execution/`
- Repo observer snapshots: `tools/report/control_plane/observer/`
- Insight artifacts: `tools/report/control_plane/insights/`
- Decision artifacts: `tools/report/control_plane/decision/`
- Proposer result artifacts: `tools/report/control_plane/proposer/`

The wrapper runs `janitor` automatically before commands and keeps local logs/artifacts for 1 day by default. Override with `CONTROL_PLANE_RETENTION_DAYS`.

## Why An Autonomy-Generated Task Exists

Every repo-aware autonomy task should be traceable through retained artifacts and provenance metadata.

A generated task can be followed back through:

- repo observer snapshot(s)
- normalized insight artifact
- guarded decision artifact
- proposer result artifact
- task provenance fields and source labels in Plane

This keeps the loop inspectable instead of opaque: operators can reconstruct why a task exists, which signal path created it, and which candidate or dedup key it came from.

## What Good Looks Like

The repo-aware autonomy loop is behaving well when:

- it proposes useful, bounded tasks without manual prompting
- it suppresses duplicate or weak proposals through dedup keys, cooldowns, and quotas
- it produces zero proposals when no strong signal exists
- every autonomy-generated task can be traced back to retained repo signals and decision artifacts
- it improves visibility and workflow quality rather than inventing open-ended work

## Current Limitations

- Local-first and single-machine only.
- Polling watchers, not webhooks.
- No queue cluster or distributed scheduler.
- No multi-machine lock manager or cross-host deduplication.
- No PR automation yet.
- No multi-repo orchestration yet.
- No production-grade supervisor beyond local `watch-all`.
- No unlimited autonomous self-generated work; proposer is bounded by guardrails.
- No automatic dependency repinning workflow during normal runs.
- No automatic end-to-end `autonomy-cycle` wrapper yet; the repo-aware autonomy stages remain intentionally explicit and inspectable.
- Repo discovery is GitHub-specific today.
- Private repo discovery depends on `GITHUB_TOKEN`; SSH alone is not enough to enumerate all accessible GitHub repos.
- Plane custom-field/native dropdown integration is not used in this local deployment; repo/branch selection is handled by Control Plane's local UI/API.

## Documentation

### Design

- [Lifecycle Contract](/home/dev/Documents/GitHub/ControlPlane/docs/design/lifecycle.md)
- [Improve Worker](/home/dev/Documents/GitHub/ControlPlane/docs/design/improve_worker.md)
- [Repo Observer](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_repo_observer.md)
- [Insight Engine](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_insight_engine.md)
- [Decision Engine](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_decision_engine.md)
- [Proposer Integration](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_proposer_integration.md)
- [Repo-Aware Autonomy Layer](/home/dev/Documents/GitHub/ControlPlane/docs/design/repo_aware_autonomy.md)
- [Execution Budget And Safety Controls](/home/dev/Documents/GitHub/ControlPlane/docs/design/execution_budget_and_safety_controls.md)
- [Plane + Kodo Wrapper Design](/home/dev/Documents/GitHub/ControlPlane/docs/design/plane_kodo_wrapper.md)

### Operator Guides

- [Setup Guide](/home/dev/Documents/GitHub/ControlPlane/docs/operator/setup.md)
- [Runtime Guide](/home/dev/Documents/GitHub/ControlPlane/docs/operator/runtime.md)
- [Diagnostics and Maintenance](/home/dev/Documents/GitHub/ControlPlane/docs/operator/diagnostics.md)

## Task Template

```text
## Execution
repo: control-plane
base_branch: main
mode: goal
allowed_paths:
  - src/
  - tests/

## Goal
Improve the autonomous Plane watcher and local workflow.

## Constraints
- Keep changes scoped to the wrapper.
- Do not modify unrelated deployment behavior.
```

Notes:

- Tasks may still be authored manually with the `## Execution` block, or created through the local repo-control UI/API. In both cases, Control Plane resolves the same structured execution contract before work runs.
- The UI/API is a convenience input layer, not a separate execution model.
- `mode: goal` is the supported runtime mode today.
- `allowed_paths` is enforced before commit/push.
- Kodo receives Goal/Constraints, not the `## Execution` block.
- Use labels like `task-kind: goal`, `task-kind: test`, `task-kind: improve`, and `source: manual` for explicit routing.

## Honesty Check

This repo is not trying to be a production distributed control plane yet. It is a local autonomous workflow system with clear worker lanes, explicit lifecycle handoffs, and operator-visible board/runtime feedback.
