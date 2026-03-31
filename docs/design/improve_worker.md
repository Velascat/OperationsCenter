# Improve Worker

`improve` is the board-level reasoning and stabilization lane.

It is responsible for:

- interpreting blocked work
- detecting recurring failure patterns
- creating the right bounded follow-up tasks
- routing unclear cases to human attention

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

## Triage Vocabulary

Blocked tasks are classified into a small fixed set:

- `infra_tooling`
- `validation_failure`
- `scope_policy`
- `parse_config`
- `verification_failure`
- `unknown`

This vocabulary is shared across:

- board comments
- watcher logs
- retained summaries

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

## Human Attention Routing

Improve should leave tasks visibly human-facing when the next action cannot be automated confidently, especially for:

- auth or secret issues
- local environment/tooling setup failures
- unclear Plane permission problems
- repeated `unknown` failures without a stable classification

In these cases, improve comments should make clear:

- the classification
- the reason summary
- whether a follow-up task was created
- whether human attention is required

## Relationship To Other Lanes

- `goal` implements
- `test` verifies
- `improve` interprets, stabilizes, and generates next work

This keeps the board readable and avoids mirroring every internal Kodo sub-role on the board.
