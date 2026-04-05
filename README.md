# Control Plane

Local autonomous coding workflow system that uses **Plane** as the board, **Control Plane** as the worker wrapper, and **Kodo** as the single-run coding engine.

## Primary Operator Model

Control Plane is operated through **Plane + CLI**:

1. Create and label work items in the Plane board.
2. Use `./scripts/control-plane.sh dev-up` to start the local stack.
3. Watchers poll the board and execute tasks automatically.
4. Results are written back to Plane as comments and state transitions.

For a full reproducible walkthrough see **[docs/demo.md](docs/demo.md)**. Run it as a validation ritual after any significant config or threshold change.

---

## What This System Is

- **Plane** is the board and source of truth for tasks, states, comments, and labels.
- **Control Plane** is the local autonomous wrapper that watches the board, prepares isolated workspaces, runs tasks, and writes results back.
- **Kodo** is the execution engine used inside a single task run.
- **goal**, **test**, **improve**, **propose**, and **review** are the board-facing worker lanes.
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

## Autonomy Execution Model

The repo-aware autonomy loop can be run stage-by-stage or as a single chained command.

**Stage commands (explicit, inspectable):**
- `observe-repo` writes a retained repo-state snapshot.
- `generate-insights` reads retained snapshots and emits normalized insights.
- `decide-proposals` reads retained insights and emits guarded proposal candidates.
- `propose-from-candidates` routes emitted candidates through existing proposer protections and may create bounded Plane tasks.

**Chained command (dry-run first):**
- `autonomy-cycle` runs all four stages in sequence. Dry-run by default — shows what would be proposed without creating tasks. Pass `--execute` to create real Plane tasks. Pass `--all-families` to enable all twelve candidate families (seven default + five gated).

**Threshold tuning:**
- `analyze-artifacts` reads retained decision + proposer artifacts, computes per-family emit/suppress/create rates, and prints recommendations when suppression is too high or emitted candidates never reach the board.

**Self-tuning regulation (bounded):**
- `tune-autonomy` runs a bounded self-tuning regulation loop: reads retained decision and proposer artifacts, computes per-family behavior metrics, and emits conservative threshold recommendations. Recommendation-only by default. Optional `--apply` mode (requires `CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1`) applies small bounded changes to `config/autonomy_tuning.json` with full cooldowns, quotas, and audit trail. The `DecisionEngineService` reads the tuning config at startup if it exists.

**Execution health (automatic):**
- Every `autonomy-cycle` run also reads retained execution artifacts and checks whether the system is generating useful work. If no-op rate is high or validation keeps failing, a bounded improve task is proposed automatically. No separate command needed.

## `Propose` Means Two Related Things

- In the repo-aware autonomy loop, `propose` is the stage that routes approved candidates toward task creation.
- In the worker model, the proposer lane is the board-facing path that applies final guardrails and writes Plane tasks.

The naming is intentionally close because the second is the board adapter for the first, but they are not the same boundary.

## What Works Today

### Core Workflow

- Single-task execution from a Plane work item.
- Background watchers for `goal`, `test`, `improve`, `propose`, and `review`.
- `watch-all` to launch all five local watcher lanes together.
- Structured task parsing from `## Execution`, `## Goal`, and optional `## Constraints`.
- Isolated ephemeral clone + task branch workflow.
- Repo-local bootstrap and validation execution.
- Worker comments, retained artifacts, and local heartbeat/status files.

### Repo-Aware Autonomy

- Read-only repo observer snapshots with eleven signal collectors: git context, recent commits, file hotspots, test signal, dependency drift, TODOs, execution health, lint violations (`ruff`), type errors (`ty`/`mypy`), CI check history (GitHub API), and per-task validation failure patterns.
- Read-only normalized insight generation from retained observer snapshots across thirteen derivers.
- Cross-signal correlation: lint and type violations are checked for overlap with git hotspot files; overlap boosts candidate confidence with a second corroborating signal.
- Guarded proposal-candidate generation from retained insights across twelve candidate families (seven active by default, five gated).
- Chain-aware candidate sequencing: `type_fix` is suppressed when `lint_fix` is active in the same cycle or was recently emitted; `execution_health_followup` is suppressed when `test_visibility` is active. Both suppression reasons are artifact-visible.
- Blast-radius guardrail: candidates whose `distinct_file_count` exceeds `max_changed_files` (default 30) are suppressed with reason `scope_too_broad` and logged in the decision artifact.
- Validation profiles in every created task: `validation_profile` (`ruff_clean`, `ty_clean`, `tests_pass`, `ci_green`, `manual_review`), `requires_human_approval`, and `evidence_schema_version` appear in every task's `## Provenance` block.
- Structured `EvidenceBundle` in decision artifact for `lint_fix` and `type_fix`: machine-readable count, `distinct_file_count`, delta, trend, top codes, and source.
- Candidate-driven Plane task creation through the proposer lane with provenance and dedup protection.
- End-to-end coverage of the repo-aware autonomy handoff chain from observer -> insights -> decision -> proposer.
- Improve-worker blocked-task triage, repeated-failure pattern detection, and bounded follow-up task creation.
- Proposer idle-board task generation with cooldowns, quotas, deduplication, velocity cap, and staleness guard.
- Dependency drift reporting with optional Plane improve-task creation.
- **Execution health self-tuning loop**: on every `autonomy-cycle` the observer reads retained kodo_plane execution artifacts, derives `high_no_op_rate` and `persistent_validation_failures` insights, and automatically proposes bounded improve tasks when execution quality degrades. No manual trigger or consecutive-run threshold required.
- **Bounded self-tuning regulator** (`tune-autonomy`): aggregates per-family metrics from retained artifacts and emits conservative threshold recommendations. Optional auto-apply mode (double-gated) writes bounded changes to `config/autonomy_tuning.json` with full audit trail.
- **Autonomy tier management** (`autonomy-tiers`): operator CLI to promote or demote families between tier 0 (decision artifact only), tier 1 (Backlog), and tier 2 (Ready for AI).

### PR Automation with Review Loop

- After a successful push, Control Plane opens a PR and enters a two-phase review loop.
- Opt-in per repo via `await_review: true` in `config/control_plane.local.yaml`.
- A dedicated `review` watcher polls GitHub every 60 seconds and drives the loop:

**Phase 1 — Self-review (automatic):**
  - Kodo reads the diff against the base branch and writes a verdict (`LGTM` or `CONCERNS`).
  - `LGTM` → squash-merge, delete branch, task marked Done.
  - `CONCERNS` → kodo runs a revision pass on the branch, then re-reviews (up to `max_self_review_loops`, default 2).
  - If still unresolved after all loops → escalate to Phase 2.

**Phase 2 — Human review (escalated):**
  - Watcher posts a comment on the PR explaining what it couldn't resolve.
  - 👍 on the PR or the latest bot reply → squash-merge + done.
  - Human comment → kodo runs a revision pass; bot replies when done; repeat up to 3 times.
  - 👍 on bot reply → merge.
  - No action after 1 day → merge automatically (timeout fallback).

**Bot safety contract:**
  - All bot-posted comments carry a `<!-- controlplane:bot -->` marker so they are never mistaken for human review requests.
  - `reviewer.bot_logins` in config lists GitHub accounts whose comments are always ignored.
  - `reviewer.allowed_reviewer_logins` optionally restricts human-phase revisions to a whitelist of logins.

- Token resolution: per-repo `token_env` if set, otherwise falls back to the global `git.token_env`.
- PR creation and merge failures are logged but do not block the task from completing.
- Set `CONTROL_PLANE_PR_DRY_RUN=1` to log intended PR actions without touching GitHub.
- Existing open PRs are backfilled into the review loop on watcher startup: `./scripts/control-plane.sh backfill-pr-reviews`.

### Execution Safety and Self-Healing

- Execution budget enforcement, retry caps, no-op suppression, and proposal suppression when execution budget is low.
- Contract validation rejects unknown repo keys, missing goal text, and disallowed base branches early with clear Plane comments — no silent fallback.
- Retry cap auto-reset: if the last attempt on a task was more than 1 hour ago, the cap is cleared automatically so a human-unblocked task gets a clean slate.
- Merge conflict self-healing: when retrying a task whose branch is behind the base branch, Control Plane merges the base into the branch so conflict markers appear in the working tree. Kodo resolves them as part of the task. No manual rebase needed.

### Repo and Branch Selection

- Repo is selected via a `repo: <key>` label on the Plane work item — no separate UI needed.
- Branch defaults to the repo's `default_branch` from config when not specified in the task description.
- Proposer scope is controlled by `propose_enabled: true/false` per repo in `config/control_plane.local.yaml`.
- Unknown repo keys and disallowed branches are rejected at task-start with a Plane comment explaining the failure.

## Lifecycle Contract

```text
goal -> review
goal -> test -> done
goal -> blocked -> improve -> follow-up goal/test or human attention
test -> goal when verification fails
improve -> bounded follow-up work, not open-ended rewrites
propose -> bounded board tasks when the board is idle or recent signals justify it
```

## Configuration

Two files drive local setup — both are gitignored. Committed templates document all available keys:

| Template | Copy to | Purpose |
|----------|---------|---------|
| `config/control_plane.example.yaml` | `config/control_plane.local.yaml` | Repos, branches, Plane connection, git behaviour, execution engine |
| `.env.control-plane.example` | `.env.control-plane.local` | Secrets and runtime knobs — never commit real values |

The split is intentional: config yaml holds structure and behaviour (safe to version), env file holds secrets (gitignored). `token_env` fields in the config yaml reference env var **names**, not values.

Key per-repo config options:

- `await_review: true` — open a PR and enter the review loop instead of auto-merging
- `bootstrap_commands: [...]` — custom install steps for non-Python repos (replaces Python venv setup when set)
- `propose_enabled: true/false` — controls whether the proposer watcher targets this repo

## Fastest Happy Path

```bash
./scripts/control-plane.sh setup
source .env.control-plane.local
./scripts/control-plane.sh dev-up
./scripts/control-plane.sh dev-status
```

Then:

1. Create a Plane work item in the configured project.
2. Add labels: `repo: <key>` (e.g. `repo: ControlPlane`) and `task-kind: goal`.
3. Optionally write a `## Goal` section in the description, or just write the goal as plain text.
4. Move it to `Ready for AI`.
5. Watch the local loop with:

```bash
./scripts/control-plane.sh dev-status
```

## Core Commands

```bash
./scripts/control-plane.sh setup
./scripts/control-plane.sh start
./scripts/control-plane.sh stop
./scripts/control-plane.sh plane-status
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
./scripts/control-plane.sh dev-up
./scripts/control-plane.sh dev-down
./scripts/control-plane.sh dev-restart
./scripts/control-plane.sh dev-status
./scripts/control-plane.sh observe-repo
./scripts/control-plane.sh generate-insights
./scripts/control-plane.sh decide-proposals
./scripts/control-plane.sh decide-proposals --dry-run
./scripts/control-plane.sh propose-from-candidates
./scripts/control-plane.sh propose-from-candidates --dry-run
./scripts/control-plane.sh autonomy-cycle
./scripts/control-plane.sh autonomy-cycle --execute
./scripts/control-plane.sh autonomy-cycle --execute --all-families
./scripts/control-plane.sh analyze-artifacts
./scripts/control-plane.sh analyze-artifacts --repo ControlPlane --limit 20
./scripts/control-plane.sh tune-autonomy
./scripts/control-plane.sh tune-autonomy --window 30
./scripts/control-plane.sh autonomy-tiers show
./scripts/control-plane.sh autonomy-tiers set --family lint_fix --tier 2
./scripts/control-plane.sh backfill-pr-reviews
./scripts/control-plane.sh plane-doctor --task-id TASK-123
./scripts/control-plane.sh smoke --task-id TASK-123 --comment-only
./scripts/control-plane.sh dependency-check
./scripts/control-plane.sh janitor
```

## CI and Local Validation

Three checks run on every push and PR (`.github/workflows/ci.yml`):

- **ruff** — lint and style
- **ty** — type checking (`ty check src/`)
- **pytest** — tests

Local equivalent:

```bash
ruff check .
ty check src/
pytest -q
```

`ty` is the active type-checking tool. `mypy` is not used or required.

---

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
- `CONTROL_PLANE_WATCH_INTERVAL_REVIEW_SECONDS`
- `CONTROL_PLANE_PR_DRY_RUN` — set to `1` to log intended PR actions without touching GitHub

Skipping is treated as a valid outcome when execution is not justified. Budget skips, no-op skips, retry-cap blocks, and proposal suppression are written to retained artifacts and surfaced in watcher logs.

## Local Stack Startup

The easiest way to bring up the whole local system is:

```bash
./scripts/control-plane.sh dev-up
```

That starts:

- Plane on `http://localhost:8080`
- all five watcher lanes: `goal`, `test`, `improve`, `propose`, and `review`

Useful companions:

- `./scripts/control-plane.sh dev-status`
- `./scripts/control-plane.sh dev-down`
- `./scripts/control-plane.sh dev-restart`

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
- Tuning run artifacts: `tools/report/control_plane/tuning/`
- Proposal feedback records: `state/proposal_feedback/`
- Autonomy cycle reports: `logs/autonomy_cycle/`

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
- when execution quality degrades (high no-op rate, recurring validation failures), it surfaces an improve task automatically rather than silently continuing to generate unhelpful work

## Current Limitations

- Local-first and single-machine only.
- Polling watchers, not webhooks.
- No queue cluster or distributed scheduler.
- No multi-machine lock manager or cross-host deduplication.
- PR automation is opt-in per repo; branch protection rules on GitHub may block auto-merge if required status checks are not satisfied.
- No multi-repo orchestration yet.
- No production-grade supervisor beyond local `watch-all`.
- No unlimited autonomous self-generated work; proposer is bounded by guardrails.
- No automatic dependency repinning workflow during normal runs.

## Documentation

### Design

- [Lifecycle Contract](/home/dev/Documents/GitHub/ControlPlane/docs/design/lifecycle.md)
- [Improve Worker](/home/dev/Documents/GitHub/ControlPlane/docs/design/improve_worker.md)
- [Repo Observer](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_repo_observer.md)
- [Insight Engine](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_insight_engine.md)
- [Decision Engine](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_decision_engine.md)
- [Proposer Integration](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_proposer_integration.md)
- [Repo-Aware Autonomy Layer](/home/dev/Documents/GitHub/ControlPlane/docs/design/repo_aware_autonomy.md)
- [Self-Tuning Regulator](/home/dev/Documents/GitHub/ControlPlane/docs/design/autonomy_self_tuning_regulator.md)
- [Execution Budget And Safety Controls](/home/dev/Documents/GitHub/ControlPlane/docs/design/execution_budget_and_safety_controls.md)
- [Plane + Kodo Wrapper Design](/home/dev/Documents/GitHub/ControlPlane/docs/design/plane_kodo_wrapper.md)
- [Roadmap](docs/design/roadmap.md)

### Operator Guides

- [Golden-Path Demo](docs/demo.md) — start here; also use as a post-change validation ritual
- [Setup Guide](docs/operator/setup.md)
- [Runtime Guide](docs/operator/runtime.md) — commands, watcher roles, dry-run-first posture
- [Autonomy Threshold Tuning](docs/operator/tuning.md) — per-family thresholds, analyze-artifacts loop, tune-autonomy regulation loop
- [PR Review Loop Guide](docs/operator/pr_review.md) — two-phase review, guardrails, troubleshooting
- [Diagnostics and Maintenance](docs/operator/diagnostics.md)

### Roadmap

- [Backlog](docs/backlog.md)

## Task Template

The minimum required to create a task manually in Plane:

**Labels:** `repo: ControlPlane`, `task-kind: goal`

**Description (plain):**
```text
Fix the config parsing bug that causes repeated failures on startup.
```

Branch defaults to the repo's `default_branch` from config. For full control, use the `## Execution` block:

```text
## Execution
repo: ControlPlane
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

- `repo: <key>` label is the primary way to set the target repo. The `## Execution` block is optional for manually created tasks.
- `base_branch` defaults to the repo's `default_branch` in config when omitted.
- `mode: goal` is the supported runtime mode today.
- `allowed_paths` is enforced before commit/push.
- Kodo receives Goal/Constraints, not the `## Execution` block.
- Use labels like `task-kind: goal`, `task-kind: test`, `task-kind: improve`, and `source: manual` for explicit routing.

## Honesty Check

This repo is not trying to be a production distributed control plane yet. It is a local autonomous workflow system with clear worker lanes, explicit lifecycle handoffs, and operator-visible board/runtime feedback.
