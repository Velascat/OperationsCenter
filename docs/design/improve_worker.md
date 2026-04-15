# Improve Worker

`improve` is the board-level reasoning and stabilization lane.

It is responsible for:

- interpreting blocked work
- detecting recurring failure patterns
- creating the right bounded follow-up tasks
- routing unclear cases to human attention
- scanning for merge conflicts, stale PRs, and review requests
- detecting post-merge CI regressions

Its default rule is:

```text
tasks first, code second
```

## Inputs Improve Uses

Priority order:

1. blocked tasks
2. recent worker comments/results
3. retained summaries for recent failures
4. explicit `task-kind: improve` tasks
5. open PR state (merge conflicts, stale PRs, review feedback)

## Triage Vocabulary

Blocked tasks are classified into a small fixed set:

- `infra_tooling`
- `validation_failure`
- `flaky_test`
- `scope_policy`
- `parse_config`
- `verification_failure`
- `context_limit`
- `dependency_missing`
- `awaiting_input`
- `unknown`

Additionally, `pre_exec_rejected` is used by the goal watcher when a task fails pre-execution validation before any Kodo run begins.

`awaiting_input` is set when Kodo embeds a `<!-- cp:question: ... -->` HTML comment in its output, indicating it stopped to ask a clarifying question rather than proceeding with an assumption. The improve watcher scans for human replies every 8 cycles and re-queues the task automatically once a reply is detected.

This vocabulary is shared across board comments, watcher logs, and retained summaries.

## Improve Decision Model

Improve uses a small internal triage result with:

- `classification`
- `certainty`
- `reason_summary`
- `recommended_action`
- `human_attention_required`
- optional bounded follow-up task spec

This keeps improve behavior explainable and testable.

## What Improve Is Allowed To Do

- add a triage comment to a blocked task
- create one bounded follow-up `goal` task
- create one bounded follow-up `test` task
- create one explicit `improve` task when more analysis is warranted
- leave a task marked for human attention
- create `[Rebase]` tasks for PRs with merge conflicts
- create `[Revise]` tasks for PRs with `CHANGES_REQUESTED` reviews
- close stale PRs and requeue originating tasks to Backlog
- create regression tasks for post-merge CI failures

## Periodic Scans (improve watcher, cycle-gated)

All scans run on the primary slot only when `parallel_slots > 1`.

| Scan | Frequency | What it does |
|------|-----------|-------------|
| Review revision | every 3 cycles | Detects `CHANGES_REQUESTED` reviews on open PRs; creates `[Revise]` tasks |
| Merge conflict | every 5 cycles | Checks `mergeable==false`; attempts rebase; creates `[Rebase]` task on failure |
| Post-merge regression | every 10 cycles | Checks merged PR CI status; creates regression tasks for failures |
| Feedback loop scan | every 15 cycles | Checks Done tasks' PR state on GitHub; auto-records merged/closed outcomes to `state/proposal_feedback/`; captures human rejections from Cancelled autonomy tasks (see below) |
| Stale PR TTL | every 20 cycles | Closes PRs older than `stale_pr_days` (default 7); requeues to Backlog |
| Workspace health | every 25 cycles | Verifies venv python per repo; attempts bootstrap repair on failure; creates `[Workspace]` task if repair fails |
| Stale autonomy scan | every 30 cycles | Cancels autonomy-proposed Backlog tasks older than 21 days whose signal is stale |
| Awaiting-input scan | every 8 cycles | Finds blocked `awaiting_input` tasks, checks for human reply, injects answer and re-queues |
| Priority rescore scan | every 45 cycles | Demotes backlog autonomy tasks with calibration acceptance <40% (adds `signal_stale` label); promotes those >75% to `priority: high` |

## Stale Autonomy Task Invalidation

Every 30 improve cycles, `handle_stale_autonomy_task_scan` scans Backlog tasks with `source: autonomy` that are older than 21 days. It cancels them with a `<!-- cp:stale-autonomy-scan -->` marker comment. If the underlying signal reappears (the proposer still finds the same issue in a newer snapshot), the task will be recreated fresh.

The marker prevents the feedback loop scan from treating these system-cancelled tasks as human rejections.

## Human Rejection Signal Capture

The feedback loop scan (Part B, every 15 cycles) also scans Cancelled tasks with `source: autonomy`. If a cancelled task does NOT carry the stale-autonomy-scan marker, it is treated as a human rejection:

1. An `abandoned` feedback record is written to `state/proposal_feedback/<id>.json`.
2. The task's `candidate_dedup_key` is registered in `ProposalRejectionStore` (`state/proposal_rejections.json`) for permanent suppression.

This "no" signal is indefinite — the proposer will never recreate the same candidate unless the rejection record is manually removed.

## Context Handoff for `context_limit` Tasks

When a task fails with `context_limit`, the follow-up task includes a `prior_progress:` block extracted from the previous execution's summary. This lets Kodo continue from where it stopped rather than restarting from scratch.

## Flaky Test Detection

The improve watcher tracks per-command validation outcomes. When a command has failed ≥30% of its last 10 runs, it is classified as `flaky_test` rather than `validation_failure`. The follow-up task targets stabilizing the test rather than retrying the original goal.

## Feedback Loop Automation

Every 15 improve cycles, `handle_feedback_loop_scan` checks Done tasks that have a
`pull_request_url` in their artifact but no `state/proposal_feedback/<id>.json` file.
It fetches the GitHub PR state and writes the feedback record automatically.  This closes
the learning loop for PRs merged or closed by humans outside the review watcher.

## Workspace Health Monitoring

Every 25 improve cycles, `handle_workspace_health_check` runs a quick sanity check
(`python -c "import sys; sys.exit(0)"`) inside the venv of each repo with a `local_path`
configured.  On failure it attempts `RepoEnvironmentBootstrapper.prepare()`.  If bootstrap
also fails, a high-priority `[Workspace] Repair environment for <repo>` goal task is
created.  This prevents a broken venv from causing every subsequent task to fail silently
with `dependency_missing` or `infra_tooling`.

## Per-Task-Kind Running TTL

The stale-running reconciler uses per-kind timeouts rather than a single global TTL. When a task has been in `Running` state longer than its TTL, it is moved to `Blocked` for triage.

| Task kind | TTL |
|-----------|-----|
| `goal` | 120 minutes |
| `test` | 45 minutes |
| `test_campaign` | 45 minutes |
| `improve` | 30 minutes |
| `improve_campaign` | 30 minutes |
| `fix_pr` | 45 minutes |
| (other / unknown) | 90 minutes |

TTL evaluation uses `issue.updated_at`. A task that updated recently (e.g. still receiving comments from an active run) is left alone even if its wall-clock age exceeds the TTL. This prevents false-positive stale detection on long-running but active executions.

## Systemic Feedback from Escalation

When the escalation webhook fires (`should_escalate` is true — ≥5 blocked events for the same classification in 24 hours), the improve watcher now also creates a dedicated `[Systemic] Investigate recurring <classification> failures` task on the board with:

- `task-kind: improve`
- `source: improve`
- `urgency: high`

This ensures systemic issues produce actionable board work, not just a webhook notification. The task is deduplicated: if a `[Systemic]` task with the same title already exists on the board, no duplicate is created.

## Escalation

When the same classification appears in ≥5 blocked-triage events within 24 hours, an HTTP POST is sent to `escalation.webhook_url` (if configured). A cooldown prevents repeated POSTs for the same classification. This is a signal to the operator that a systemic issue exists.

## What Improve Should Avoid

- recursive unblock storms
- vague task spray
- many similar children for the same failure pattern
- broad unsolicited repo rewrites

## Current Safeguards

- duplicate avoidance for the same source task + handoff reason
- cap on follow-up tasks per improve cycle
- repeated-pattern heuristic that prefers one system-fix task over many scattered children
- no recursive unblock task generation for improve-generated failures
- stale-PR scan only closes PRs whose comment history confirms no prior close attempt

## Human Attention Routing

Improve should leave tasks visibly human-facing when the next action cannot be automated confidently, especially for:

- auth or secret issues
- local environment/tooling setup failures
- unclear Plane permission problems
- repeated `unknown` failures without a stable classification

In these cases, improve comments make clear:

- the classification
- the reason summary
- whether a follow-up task was created
- whether human attention is required

## Relationship To Other Lanes

- `propose` seeds new bounded board work when the system is otherwise quiet
- `goal` implements
- `test` verifies
- `improve` interprets, stabilizes, and generates next work

This keeps the board readable and avoids mirroring every internal Kodo sub-role on the board.
