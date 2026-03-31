# Autonomy Proposer Integration

Pass 4 connects retained decision output to the existing proposer lane.

## Flow

`observe -> analyze -> decide -> propose`

The proposer path does not read raw observer signals directly. It only creates Plane tasks from retained decision artifacts whose candidates are already marked `status=emit`.

## Responsibilities

- `candidate_loader.py`: load latest decision artifact and matching insight artifact
- `candidate_integration.py`: filter emitted candidates, apply proposer guardrails, create tasks, record results
- `candidate_mapper.py`: map a guarded candidate to a bounded Plane task draft
- `provenance.py`: carry observer, insight, decision, and proposer lineage into the task body
- `guardrail_adapter.py`: reuse open-task dedup, cooldown, and budget-aware suppression
- `artifact_writer.py`: retain `proposal_results.json` and `proposal_results.md`

## Task Contract

Created tasks include:

- `## Execution`
- `## Goal`
- `## Constraints`
- `## Proposal Provenance`

Labels include:

- `task-kind: <kind>`
- `source: autonomy`
- `source: propose`
- `source-family: <family>`

## Safety Boundaries

- only `status=emit` decision candidates may become Plane tasks
- dry-run performs full mapping and guardrails but does not write to Plane
- one candidate failure does not void the whole proposer run
- zero-created runs are valid and retained
