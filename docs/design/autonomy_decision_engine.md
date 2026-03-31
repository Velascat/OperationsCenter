# Decision Engine

Pass 3 of the autonomy layer adds a bounded decision engine.

Its job is to convert normalized insights into guarded proposal candidates and explicit suppression records.

It does **not**:

- create Plane tasks
- modify the repo
- use LLM reasoning
- generate open-ended strategy

## Role In The Larger Autonomy Flow

```text
observe -> analyze -> decide -> propose
```

This pass implements `decide`.

## Inputs

Primary input:

- `tools/report/control_plane/insights/<run_id>/repo_insights.json`

Optional bounded history:

- recent prior decision artifacts for cooldown, dedup, and quota enforcement

## Candidate Families In Scope

- test visibility
- dependency drift follow-up
- hotspot concentration
- TODO/FIXME accumulation
- observation coverage

## Guardrails

- deterministic rules
- bounded history scan
- dedup keys
- cooldown window
- max emitted candidates per run
- suppression recording for rejected concepts

## Output

Decision runs write retained artifacts under:

- `tools/report/control_plane/decision/<run_id>/proposal_candidates.json`
- `tools/report/control_plane/decision/<run_id>/proposal_candidates.md`

The resulting artifact is intended for later proposer integration, not direct Plane writeback in this pass.
