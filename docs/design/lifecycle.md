# Lifecycle Contract

Operations Center has six board-facing worker lanes:

- `goal`
- `test`
- `improve`
- `propose`
- `review`
- `spec`

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
spec -> campaign of goal/test_campaign/improve_campaign tasks when trigger fires
spec -> recovery/revision when campaign stalls; abandon when budget exhausted
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

### `spec`

- Monitors board state and external triggers (drop-file, Plane label, queue drain) to decide when to start a spec-driven campaign.
- Autonomously brainstorms what is worth building via direct Anthropic API call, producing a spec doc written to `docs/specs/`.
- Converts the spec into a bounded campaign of Plane tasks (`goal`, `test_campaign`, `improve_campaign`) covering implement → test → improve phases.
- Tracks campaign progress in `state/campaigns/active.json` (via `CampaignStateManager`).
- Runs recovery logic each cycle: revises the spec (up to 3 times) when the campaign stalls; abandons and self-cancels when the 72-hour budget is exhausted.
- Suppresses heuristic `propose` candidates that overlap an active campaign's `area_keywords`, preventing conflicting parallel board work.
- Picks up operator direction from a drop-file (`state/spec_director_trigger.md`) or a Plane task label (`spec-director: trigger`).

### `review`

- Manages open PRs created by the `goal` lane when `await_review: true` is set for the repo.
- Operates as a two-phase state machine per PR, tracked in `state/pr_reviews/<task_id>.json`:
  - **Phase 1 — self-review**: kodo reads the diff against the base branch and writes a verdict file (`LGTM` or `CONCERNS`). LGTM triggers merge. CONCERNS triggers a kodo revision pass followed by another self-review cycle (up to `reviewer.max_self_review_loops` times).
  - **Phase 2 — human review**: if self-review cannot resolve its concerns, the watcher posts an escalation comment and waits for human input. Human 👍 on the PR or bot reply triggers merge. Human comment triggers a kodo revision pass; bot replies when done (max 3 loops). Timeout of 1 day triggers auto-merge.
- All bot-posted comments carry a `<!-- operations-center:bot -->` marker. The watcher never re-processes its own comments.
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
- `spec` creates a campaign of `goal`, `test_campaign`, and `improve_campaign` tasks when a trigger fires. It does not directly implement changes.
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

## Campaign Task Kinds

The `spec` lane introduces two new task kinds for the test and improve phases of a campaign:

- `test_campaign` — picked up by the `test` role worker (alongside plain `test`); runs `kodo --test` for adversarial testing of campaign implementation.
- `improve_campaign` — picked up by the `improve` role worker (alongside plain `improve`); runs `kodo --improve` for simplification/architecture/usability passes.

Campaign `implement` phase tasks use the standard `goal` task kind and are executed by the existing `goal` lane workers unchanged.

The `ROLE_TASK_KINDS` map in `worker/main.py` controls which task kinds each role claims:

```python
ROLE_TASK_KINDS = {
    "goal":    {"goal"},
    "test":    {"test", "test_campaign"},
    "improve": {"improve", "improve_campaign"},
    "fix_pr":  {"fix_pr"},
}
```

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
