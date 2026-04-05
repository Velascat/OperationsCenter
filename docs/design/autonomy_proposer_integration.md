# Autonomy Proposer Integration

The proposer integration is the fourth stage of the autonomy pipeline. It connects retained decision output to the existing proposer lane by creating Plane tasks from emitted candidates. Execution, review, and feedback form the remaining stages that close the loop back into the observer.

## Flow

```text
observe -> analyze -> decide -> propose
```

The proposer path does not read raw observer signals directly. It only creates Plane tasks from retained decision artifacts whose candidates are already marked `status=emit`.

## Responsibilities

- `candidate_loader.py`: load latest decision artifact and matching insight artifact
- `candidate_integration.py`: filter emitted candidates, apply proposer guardrails, create tasks, record results
- `candidate_mapper.py`: map a guarded candidate to a bounded Plane task draft
- `provenance.py`: carry observer, insight, decision, and proposer lineage into the task body
- `guardrail_adapter.py`: reuse open-task dedup, cooldown, and budget-aware suppression
- `artifact_writer.py`: retain `proposal_results.json` and `proposal_results.md`

## Autonomy Tier Routing

The initial Plane task state depends on the candidate's autonomy tier (from `config/autonomy_tiers.json`):

| Tier | Initial state |
|------|--------------|
| 2 | `Ready for AI` — executes on next watcher cycle |
| 1 (style risk_class) | `Ready for AI` |
| 1 (other risk_class) | `Backlog` — human must promote |
| 0 | Not created (proposer skips tier-0 candidates) |

## Task Contract

Every created task includes the following sections in its description:

### `## Execution`

```
repo: <repo_key>
base_branch: <default_branch>
mode: goal
allowed_paths:
  - src/
  - tests/
  - docs/
```

### `## Goal`

The `summary_hint` from the candidate's `proposal_outline`. This is the task's primary objective.

### `## Constraints`

Family-specific constraints, for example:

- `lint_fix`: use `ruff check --fix`; do not suppress violations with `# noqa`
- `type_fix`: targeted annotations only; avoid broad `# type: ignore` suppressions
- `ci_pattern`: investigate root cause; do not suppress failing checks
- `validation_pattern_followup`: investigate artifacts before proposing changes
- All others: keep scope bounded; do not expand into unrelated refactors

### `## Provenance`

Full lineage carried into the task body:

```
source: autonomy
source_family: <family>
candidate_id: <uuid>
candidate_dedup_key: <dedup_key>
confidence: high|medium
risk_class: style|logic|structural|arch
autonomy_tier: <0|1|2>
validation_profile: ruff_clean|ty_clean|tests_pass|ci_green|manual_review
requires_human_approval: true|false
evidence_schema_version: 1
expires_at: <YYYY-MM-DD>
observer_run_ids:
  - <run_id>
insight_run_id: <run_id>
decision_run_id: <run_id>
proposer_run_id: <run_id>
```

`validation_profile` is derived from the candidate family (set by `profile_for_family()` in `validation_profiles.py` unless overridden by the rule). `requires_human_approval` mirrors whether the task started in `Backlog` (`true`) or `Ready for AI` (`false`). `evidence_schema_version` tracks the `EvidenceBundle` format version (currently always `1`).

### `## Evidence`

Bullet list of `evidence_lines` from the candidate — specific, factual observations that drove the proposal:

```
- 47 lint violations in src/control_plane/decision/service.py
- violation count increased from 32 in prior snapshot
```

## Labels

Every autonomy-created task carries:

- `task-kind: <kind>` — `goal` or `improve`
- `source: autonomy`
- `source: propose`
- `source-family: <family>`

## Feedback Records

The reviewer watcher writes a feedback record to `state/proposal_feedback/<task_id>.json` when it merges or escalates a PR. The decision engine's staleness guard and the self-tuning regulator both read these records.

Feedback record format:

```json
{
  "recorded_at": "2026-04-05T12:00:00Z",
  "task_id": "<plane_issue_id>",
  "outcome": "merged|escalated|abandoned|blocked",
  "source": "reviewer|manual",
  "pr_number": 42
}
```

To record feedback manually (for tasks merged outside the reviewer loop):

```bash
python -m control_plane.entrypoints.feedback.main record \
    --task-id <uuid> --outcome merged --pr-number 42
```

## Safety Boundaries

- only `status=emit` decision candidates may become Plane tasks
- dry-run performs full mapping and guardrails but does not write to Plane
- one candidate failure does not void the whole proposer run
- zero-created runs are valid and retained
