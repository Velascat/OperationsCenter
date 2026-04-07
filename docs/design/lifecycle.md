# Lifecycle Contract

Control Plane has five board-facing worker lanes:

- `goal`
- `test`
- `improve`
- `propose`
- `review`

The lanes are not independent scripts. They are stages in a board-level workflow.

## Core Flow

```text
goal -> In Review (PR opened, review watcher takes over)
goal -> test -> done
goal -> blocked -> improve -> follow-up goal/test or human attention
test -> goal when verification fails
propose -> bounded goal/test/improve tasks when the board is quiet or recent signals justify it
review -> Done (self-review LGTM or human 👍 → PR merged)
review -> human review phase (self-review unable to resolve → escalated to human)
```

## Lane Responsibilities

### `goal`

- Consumes `task-kind: goal` tasks in `Ready for AI`.
- Runs implementation work in an isolated workspace.
- Ends with one explicit next-step outcome:
  - success with no explicit verification need -> `Review`
  - success with explicit verification need -> creates `task-kind: test`
  - blocked/failure -> leaves the task `Blocked` for improve triage

### `test`

- Consumes `task-kind: test` tasks in `Ready for AI`.
- Runs verification work.
- Ends with one explicit next-step outcome:
  - verification success -> `Done`
  - verification failure -> creates `task-kind: goal`

### `improve`

- Consumes explicit `task-kind: improve` tasks.
- Regularly inspects `Blocked` tasks.
- Interprets failures, creates bounded follow-up work, or routes the task to human attention.

Blocked-task handling lives inside `improve`, not in a separate `unblocker` lane.

### `propose`

- Monitors board/system state when normal implementation and verification lanes are quiet.
- Uses bounded signals such as idle board state, repeated blocked patterns, and recent retained findings.
- Creates bounded Plane tasks instead of directly editing the repo.
- Places strong, high-confidence tasks in `Ready for AI` and lower-confidence tasks in `Backlog`.

### `review`

- Manages open PRs created by the `goal` lane when `await_review: true` is set for the repo.
- Operates as a two-phase state machine per PR, tracked in `state/pr_reviews/<task_id>.json`:
  - **Phase 1 — self-review**: kodo reads the diff against the base branch and writes a verdict file (`LGTM` or `CONCERNS`). LGTM triggers merge. CONCERNS triggers a kodo revision pass followed by another self-review cycle (up to `reviewer.max_self_review_loops` times).
  - **Phase 2 — human review**: if self-review cannot resolve its concerns, the watcher posts an escalation comment and waits for human input. Human 👍 on the PR or bot reply triggers merge. Human comment triggers a kodo revision pass; bot replies when done (max 3 loops). Timeout of 1 day triggers auto-merge.
- All bot-posted comments carry a `<!-- controlplane:bot -->` marker. The watcher never re-processes its own comments.
- `reviewer.bot_logins` in config lists accounts whose comments are always ignored.
- `reviewer.allowed_reviewer_logins` optionally restricts human-phase revision triggers to a whitelist.
- On startup, the watcher backfills state files for any open PRs that pre-date it.

## Handoff Rules

- `goal` may create `test` follow-up tasks.
- `goal` may open a PR and hand off to `review` when `await_review: true`.
- `test` may create `goal` follow-up tasks.
- `improve` may create bounded `goal`, `test`, or explicit `improve` follow-up tasks.
- `propose` may create bounded `goal`, `test`, or `improve` tasks when its guardrails allow it.
- `review` marks tasks Done on merge; does not create follow-up tasks.
- Every handoff should remain visible on the board through comments and child-task context.

## Task Lineage

Follow-up tasks include:

- `original_task_id`
- `original_task_title`
- `source_worker_role`
- `source_task_kind`
- `follow_up_task_kind`
- `handoff_reason`

The parent task receives a comment with created child task ids, and the child task receives an initial source comment.

## States Used By The Local Workflow

- `Backlog`
- `Ready for AI`
- `Running`
- `Review` — task completed, changes pushed; no PR automation
- `In Review` — PR opened; review watcher is driving merge
- `Blocked`
- `Done`

These are board-facing workflow states, not a queue implementation.

## Awaiting-Input Flow

When Kodo embeds `<!-- cp:question: ... -->` in its output, the task enters a human-in-the-loop sub-flow inside the `Blocked` state:

```text
goal [Running] -> Blocked (awaiting_input)
                      ↓
         improve extracts question, posts Plane comment
                      ↓  (every 8 cycles)
         human replies on Plane task
                      ↓
         improve injects answer, re-queues -> Ready for AI
```

This flow is distinct from `validation_failure` or `infra_tooling` blocks — it is not retried automatically. It waits for a human answer. Once detected and re-queued, the task re-enters the normal goal execution cycle with the answer injected into its description.

## Boundaries

This lifecycle contract does not imply:

- webhooks
- distributed scheduling
- queue infrastructure
- unlimited retries
- automatic requeue storms

The current implementation is a local polling workflow with explicit next-step semantics.
