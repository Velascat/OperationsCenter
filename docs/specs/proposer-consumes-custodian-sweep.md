---
status: proposed
---

# Spec: Proposer Consumes Custodian Sweep Findings

## Goal

Teach the proposer to read open `custodian-sweep:<repo>` triage tasks
in Plane and emit executable goal tasks per high-Δ detector finding,
turning the audit dashboard into autonomy fuel without an operator
manually transcribing each finding into a goal.

Today the sweep (`operations-center-custodian-sweep`) creates one
Plane task per managed repo with a markdown findings table. The
existing executor cannot run these because they lack the
`repo:` / `mode:` / `goal:` / `allowed_paths:` metadata it needs.
An operator currently has to read the table and hand-write goal
tasks for the findings worth fixing.

## Definition of Done

- Proposer recognises Plane tasks labeled `custodian-sweep` as a new
  proposal source with key `source: custodian-sweep`
- For each open sweep task, proposer parses the findings table and
  emits at most N proposals (configurable, default 3) for the
  detectors with the largest positive Δ since last sweep
- Each emitted proposal has correct metadata: `repo:`, `mode: improve`,
  `goal:` describing the detector + sample location, `allowed_paths:`
  scoped where possible from detector samples
- Dedup against existing open proposals: don't re-emit if a proposal
  for the same `(repo_key, detector_id)` is already in flight
- Sweep task is commented with "→ proposed N goal tasks" so the
  operator can trace what came from which sweep
- Tests cover: empty sweep, sweep with no positive deltas (no
  proposals), sweep with mixed deltas (only positives proposed), and
  the dedup path

## Architecture

### New proposal source

The proposer already has multiple sources (autonomy gaps,
backlog promotion, regression fixes, …). Add `custodian-sweep`
alongside them. The source reads from Plane, not from disk —
sweep results are persisted in Plane comments + the
`state/custodian_sweep/last_sweep.json` snapshot, so the
proposer only needs Plane access.

### Sweep-task body parser

The sweep emits a markdown body whose shape is fixed
(`_render_body` in `entrypoints/custodian_sweep/main.py`):

```
| Detector | Status | Count | Δ since last sweep |
|---|---|---:|---:|
| `C3` print statements outside ... | open | 14 | +2 |
...
```

A small parser extracts `(detector_id, count, delta)` rows. Sweep
errors ("Custodian sweep error for X") are recognised and skipped
without emitting proposals.

### Goal-text rendering

For a detector row, the goal-text is generated from the detector's
description and (optionally) the first few `samples` from the
last-sweep snapshot:

```
goal: |
  Reduce C3 (print statements outside dispatch) findings in
  OperationsCenter from 14 to <=12 (current Δ +2 since last sweep).
  Target locations include: src/operations_center/foo.py:42,
  src/operations_center/bar.py:118.
mode: improve
allowed_paths:
  - src/operations_center/foo.py
  - src/operations_center/bar.py
```

`allowed_paths` is derived from sample paths when they all share a
prefix; otherwise omitted (executor falls back to repo-wide).

### Dedup

`(repo_key, detector_id)` is the dedup key. Look for any open
proposal with `source: custodian-sweep` whose body declares the same
detector ID. Skip if found. This prevents repeated sweeps from
piling up duplicate proposals for the same long-standing finding.

## Open questions

- Should the proposer also handle *negative* deltas (regressions
  resolved) by closing related sweep proposals? Probably yes, but
  defer to a follow-up — first ship the positive-Δ path.
- How aggressive is the default cap? `3 per repo per sweep` keeps
  the queue calm; bump after observing real volume.
- Should sweep tasks themselves auto-close when all their detectors
  return to zero? Out of scope here; belongs to the sweep entrypoint,
  not the proposer.

## Out of scope

- Auto-execution. `await_review: true` on every repo means proposals
  still need explicit approval; this spec doesn't change that.
- Cross-detector aggregation (e.g. "fix all C3 issues in one PR").
  Each proposal targets one detector; bundling is a future
  optimisation if PR volume becomes a problem.
- Teaching Custodian itself about Plane. Custodian stays read-only
  and Plane-unaware — the proposer is the bridge.

## Files to touch (when implemented)

- `src/operations_center/proposer/sources/` — new `custodian_sweep.py`
  source module
- `src/operations_center/proposer/main.py` — register the new source
- `tests/proposer/test_custodian_sweep_source.py` — new tests
- `docs/specs/proposer-consumes-custodian-sweep.md` — flip front
  matter `status:` to `active` then `done`

## Related

- `docs/architecture/audit_architecture.md` — the broader
  Custodian/OC split this spec sits inside
- `src/operations_center/entrypoints/custodian_sweep/main.py` —
  the producer side; its `_render_body` is the contract this
  parser must track
