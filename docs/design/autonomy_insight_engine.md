# Insight Engine

Pass 2 of the autonomy layer adds a bounded insight engine.

Its job is to convert retained repo observer snapshots into normalized, machine-readable findings.

It does **not**:

- create Plane tasks
- decide what work should happen next
- rank severity
- modify the repo

## Role In The Larger Autonomy Flow

```text
observe -> analyze -> decide -> propose
```

This pass implements `analyze`.

## Inputs

Primary input:

- `tools/report/control_plane/observer/<run_id>/repo_state_snapshot.json`

The insight engine may also read a bounded recent snapshot history for the same repo.

## Insight Kinds In Scope

- dirty working tree
- commit activity
- file hotspot patterns
- test status continuity
- dependency drift continuity
- TODO/FIXME concentration
- observation coverage
- execution health

## Execution Health Insights

`ExecutionHealthDeriver` reads `signals.execution_health` from the most recent snapshot and derives up to two insights per repo:

| Pattern | Condition | Dedup key suffix |
|---------|-----------|-----------------|
| `high_no_op_rate` | ≥50% of runs were no-ops and total_runs ≥ 5 | `high_no_op_rate` |
| `persistent_validation_failures` | validation_failed_count ≥ 3 | `persistent_validation_failures` |

These are factual observations about execution behaviour, not recommendations. The decision engine converts them into candidates.

## Output

Insight runs write retained artifacts under:

- `tools/report/control_plane/insights/<run_id>/repo_insights.json`
- `tools/report/control_plane/insights/<run_id>/repo_insights.md`

The JSON artifact is the primary machine-consumable contract for later decision logic.

## Design Constraints

- factual
- bounded
- deterministic
- read-only

The engine should answer:

> what is observably happening?

It should not answer:

> what should we do about it?
