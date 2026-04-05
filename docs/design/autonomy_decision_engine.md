# Decision Engine

The decision engine is the third stage of the autonomy pipeline. It converts normalized insights into guarded proposal candidates and explicit suppression records.

It does **not**:

- create Plane tasks
- modify the repo
- use LLM reasoning
- generate open-ended strategy

## Role In The Larger Autonomy Flow

```text
observe -> analyze -> decide -> propose
```

## Inputs

Primary input:

- `tools/report/control_plane/insights/<run_id>/repo_insights.json`

Optional bounded history:

- recent prior decision artifacts for cooldown, dedup, and quota enforcement
- prior proposer artifacts for velocity cap and staleness guard
- feedback records from `state/proposal_feedback/` for staleness guard

## Candidate Families

### Default (fire on every cycle)

| Family | Rule | Signal source | Risk class |
|--------|------|---------------|------------|
| `observation_coverage` | `ObservationCoverageRule` | observer coverage gaps | logic |
| `test_visibility` | `TestVisibilityRule` | test status continuity | logic |
| `dependency_drift` | `DependencyDriftRule` | dependency drift continuity | logic |
| `execution_health_followup` | `ExecutionHealthRule` | execution artifact outcomes | logic |
| `lint_fix` | `LintDriftRule` | lint violation signal | style |
| `type_fix` | `TypeImprovementRule` | type error signal | logic |
| `validation_pattern_followup` | `ValidationPatternRule` | per-task validation failure patterns | logic |

### Gated (require `--all-families` or explicit `allowed_families`)

| Family | Rule | Risk class | Promotion criteria |
|--------|------|------------|--------------------|
| `ci_pattern` | `CIPatternRule` | logic | enable once CI history baseline is established |
| `hotspot_concentration` | `HotspotConcentrationRule` | structural | confirm hotspot signals identify decomposable files |
| `todo_accumulation` | `TodoAccumulationRule` | style | confirm TODO markers represent real debt |
| `backlog_promotion` | `BacklogPromotionRule` | logic | enable when backlog hygiene is a priority |
| `arch_promotion` | `ArchPromotionRule` | arch | manual promotion only; requires architectural review |

## Candidate Fields

Every emitted candidate carries:

| Field | Description |
|-------|-------------|
| `family` | which candidate family fired |
| `confidence` | `"high"` or `"medium"` — set by the rule based on signal strength |
| `risk_class` | `style`, `logic`, `structural`, or `arch` — determines default task state |
| `dedup_key` | stable key used for cooldown and open-task dedup |
| `expires_after_runs` | how many autonomy cycles before an unresolved candidate is considered stale |
| `evidence_lines` | bullet list of specific evidence from the signal |
| `proposal_outline.title_hint` | suggested Plane task title |
| `proposal_outline.summary_hint` | suggested task description |

## Policy Guardrails

### Cooldown

Candidates with the same `dedup_key` as a recently emitted candidate are suppressed with reason `cooldown_active`. Default window: 120 minutes.

### Per-family quota

At most 1 candidate per family per decision run. Additional candidates from the same family are suppressed with reason `quota_exceeded`.

### Max candidates

Total emitted candidates per run is bounded by `max_candidates` (default 3).

### Family gating

Families not in `allowed_families` are suppressed with reason `family_deferred_initial_gating`.

### Open-task dedup

If a Plane task with the same dedup key is already open (not merged/closed), the candidate is suppressed with reason `existing_open_equivalent_task`.

### Min-confidence gate

Rules may require minimum signal strength. Candidates below the threshold are not emitted.

### Velocity cap

If ≥ `max_proposals_per_24h` (default 10) proposal candidates were created in the last 24 hours, all candidates for this run are suppressed with reason `velocity_cap_exceeded`. Prevents burst creation from stale artifact windows.

### Staleness guard

Before emitting a candidate, the decision engine checks whether a prior unresolved proposal with the same dedup key has exceeded its `expires_after_runs` window without a feedback record. Stale open proposals are suppressed with reason `proposal_stale_open`. This prevents re-proposing the same task indefinitely when the prior instance was never acted on.

## Suppression Records

Every suppressed candidate is recorded in the decision artifact with:

- `dedup_key`
- `reason` — one of the suppression reasons above
- `family`
- `suppressed_at`

This makes every non-emission auditable.

## Autonomy Tiers

Each candidate family has an assigned **autonomy tier** that controls what Plane task state the created task starts in:

| Tier | Behavior |
|------|----------|
| 0 | Do not auto-create. Candidate appears in decision artifact only. |
| 1 | Auto-create in `Backlog`. Human must promote to `Ready for AI` to execute. |
| 2 | Auto-create in `Ready for AI`. Executes immediately on the next watcher cycle. |

Tier is read from `config/autonomy_tiers.json` (managed by the `autonomy-tiers` CLI). If no override exists, defaults apply:

| Family | Default tier |
|--------|-------------|
| `lint_fix` | 2 |
| `observation_coverage` | 1 |
| `test_visibility` | 1 |
| `dependency_drift` | 1 |
| `execution_health_followup` | 1 |
| `type_fix` | 1 |
| `ci_pattern` | 1 |
| `validation_pattern_followup` | 1 |
| `hotspot_concentration` | 1 |
| `todo_accumulation` | 1 |
| `arch_promotion` | 0 |

To change a tier:

```bash
./scripts/control-plane.sh autonomy-tiers set --family lint_fix --tier 1
./scripts/control-plane.sh autonomy-tiers show
```

## Output

Decision runs write retained artifacts under:

- `tools/report/control_plane/decision/<run_id>/proposal_candidates.json`
- `tools/report/control_plane/decision/<run_id>/proposal_candidates.md`

The resulting artifact is consumed by the proposer integration stage.
