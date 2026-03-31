# Repo Observer

Pass 1 of the autonomy layer adds a read-only repo observer.

Its job is simple:

- inspect a local repo without modifying it
- collect a bounded set of factual signals
- emit one normalized snapshot artifact per run

It does **not**:

- score or rank work
- create Plane tasks
- decide what should happen next
- modify the observed repo

## Role In The Larger Autonomy Flow

```text
observe -> analyze -> decide -> propose
```

This pass only implements `observe`.

## Signals In Scope

- git branch context
- recent commit metadata
- changed-file hotspot summary
- last known local test signal from existing Control Plane logs/artifacts if available
- dependency drift signal from existing dependency-check artifacts if available
- TODO/FIXME summary
- observation metadata

## Output

Observer runs write retained artifacts under:

- `tools/report/control_plane/observer/<run_id>/repo_state_snapshot.json`
- `tools/report/control_plane/observer/<run_id>/repo_state_snapshot.md`

The JSON snapshot is the primary machine-consumable contract for later passes.

## Guardrails

- read-only against the observed repo
- bounded signal collection
- no hidden network dependency required for a successful local run
- partial collector failures are represented in output instead of failing the whole snapshot

## Intended Follow-On

Later passes should consume these snapshots and convert them into:

- normalized insights
- guarded decisions
- bounded Plane proposals
