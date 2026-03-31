# Proposer Integration

Pass 4 of the repo-aware autonomy layer takes emitted proposal candidates and routes them through the existing proposer-facing Plane task creation flow.

It does **not**:

- bypass existing proposer protections
- create work from raw observer snapshots or raw insights
- modify the repo directly
- execute created tasks in the same command path

## Role In The Larger Autonomy Flow

```text
observe -> analyze -> decide -> propose
```

This pass implements `propose`.

## Inputs

Primary input:

- `tools/report/control_plane/decision/<run_id>/proposal_candidates.json`

Supporting input:

- the matching retained insight artifact for provenance
- existing open Plane tasks for dedup/open-work checks
- bounded proposer result history for cooldown checks

## Output

Proposer integration runs write retained artifacts under:

- `tools/report/control_plane/proposer/<run_id>/proposal_results.json`
- `tools/report/control_plane/proposer/<run_id>/proposal_results.md`

The JSON artifact records:

- created tasks
- skipped candidates
- failed candidates
- source decision run id
- dry-run state

## Provenance Contract

Created Plane tasks carry a provenance section that traces back to:

- observer run id(s)
- insight run id
- decision run id
- candidate id and dedup key
- proposer integration run id
- source family

## Guardrails

- create Plane tasks only from emitted Pass 3 candidates
- preserve dedup, cooldown, and open-task checks
- keep task payloads bounded and factual
- treat zero-created runs as valid and observable
- do not create broad umbrella tasks like `Improve the repo`
