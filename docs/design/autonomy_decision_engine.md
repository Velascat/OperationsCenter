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

## Candidate Families

### Default (active without flags)

| Family | Rule | Signal source |
|--------|------|---------------|
| `observation_coverage` | `ObservationCoverageRule` | observer coverage gaps |
| `test_visibility` | `TestVisibilityRule` | test status continuity |
| `dependency_drift` | `DependencyDriftRule` | dependency drift continuity |
| `execution_health_followup` | `ExecutionHealthRule` | execution artifact outcomes |

### Gated (require `--all-families` or explicit `allowed_families`)

| Family | Rule | Promotion criteria |
|--------|------|--------------------|
| `hotspot_concentration` | `HotspotConcentrationRule` | confirm hotspot signals identify genuinely decomposable files |
| `todo_accumulation` | `TodoAccumulationRule` | confirm TODO markers represent real debt, not intentional markers |

### `execution_health_followup`

Fires on two patterns derived from retained execution artifacts:

- **`high_no_op_rate`** — most recent runs for a repo produced no code changes. Suggests reviewing task quality or proposer heuristics.
- **`persistent_validation_failures`** — multiple executed runs failed the post-execution validation step. Suggests a systemic quality issue or tasks scoped beyond one execution pass.

This family is in `_DEFAULT_ALLOWED_FAMILIES` because its signals are derived directly from observed execution behaviour rather than from consecutive-run thresholds, making false-positive suppression less likely.

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
