# Lifecycle Contract

Control Plane has three board-facing worker lanes:

- `goal`
- `test`
- `improve`

The lanes are not independent scripts. They are stages in a board-level workflow.

## Core Flow

```text
goal -> review
goal -> test -> done
goal -> blocked -> improve -> follow-up goal/test or human attention
test -> goal when verification fails
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

## Handoff Rules

- `goal` may create `test` follow-up tasks.
- `test` may create `goal` follow-up tasks.
- `improve` may create bounded `goal`, `test`, or explicit `improve` follow-up tasks.
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
- `Review`
- `Blocked`
- `Done`

These are board-facing workflow states, not a queue implementation.

## Boundaries

This lifecycle contract does not imply:

- webhooks
- distributed scheduling
- queue infrastructure
- unlimited retries
- automatic requeue storms

The current implementation is a local polling workflow with explicit next-step semantics.
