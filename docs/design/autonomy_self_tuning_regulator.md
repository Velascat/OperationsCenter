# Autonomy Self-Tuning Regulator

Control Plane includes a bounded self-tuning regulation loop that uses retained artifact evidence to recommend and optionally apply conservative threshold adjustments to the decision engine.

This is **not** an open-ended self-modification system. It is a bounded regulator with explicit guardrails.

## Core Principle

The regulator follows a meta-control model:

```text
retained autonomy artifacts
  -> aggregate family metrics
  -> evaluate bounded tuning rules
  -> emit recommendations
  -> optionally apply safe changes (opt-in, guarded)
  -> retain full audit trail
```

This is separate from, and never a hot-path side effect of, the main autonomy loop:

```text
observe -> analyze -> decide -> propose
```

## Regulation Loop

```
tools/report/control_plane/decision/*/proposal_candidates.json
tools/report/control_plane/proposer/*/proposal_results.json
                  ↓
          MetricsAggregator
    (per-family emit/suppress/create rates)
                  ↓
        RecommendationEngine
    (explicit deterministic rules)
                  ↓
     recommendations (always retained)
                  ↓
      TuningGuardrails (if --apply)
    cooldown / quota / range / allowlist
                  ↓
        TuningApplier (if --apply)
    writes config/autonomy_tuning.json
                  ↓
    DecisionEngineService reads overrides
    on next autonomy-cycle run
```

## Operating Modes

### Recommendation-only (default)

```bash
./scripts/control-plane.sh tune-autonomy
```

- Reads retained decision and proposer artifacts.
- Computes per-family behavior metrics.
- Emits conservative tuning recommendations.
- Writes retained artifacts to `tools/report/control_plane/tuning/<run_id>/`.
- **Does not modify any config**.

### Auto-apply (opt-in)

```bash
CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1 ./scripts/control-plane.sh tune-autonomy --apply
```

Two conditions must both be true:
1. `--apply` flag is passed.
2. `CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1` env var is set.

This double-gate prevents accidental application.

When active, applies small bounded threshold changes to `config/autonomy_tuning.json`. The `DecisionEngineService` reads this file at startup if it exists, overriding default thresholds.

## Metrics Aggregated

For each candidate family, over the analysis window (default last 20 decision runs):

| Metric | Description |
|--------|-------------|
| `candidates_emitted` | From decision artifacts, status == "emit" |
| `candidates_suppressed` | From decision suppressed list |
| `candidates_created` | From proposer created list |
| `candidates_skipped` | From proposer skipped list |
| `candidates_failed` | From proposer failed list |
| `suppression_rate` | suppressed / (emitted + suppressed) |
| `create_rate` | created / emitted |
| `no_creation_rate` | (emitted - created) / emitted |
| `top_suppression_reasons` | Top 5 suppression reason counts |

Dry-run artifacts are excluded. Proposer artifacts are correlated by `source_decision_run_id`.

## Recommendation Rules

All rules require at least 5 sample runs. Below this floor: `no_data`.

| Pattern | Condition | Action |
|---------|-----------|--------|
| Over-suppressed | suppression_rate ≥ 90% | `loosen_threshold` |
| Noisy/low-value | emitted ≥ 5 AND create_rate ≤ 10% | `tighten_threshold` |
| Healthy | emitted ≥ 3 AND create_rate ≥ 25% | `keep` |
| Silent | emitted == 0 AND suppressed == 0 | `review` |
| Insufficient data | sample_runs < 5 | `no_data` |
| Moderate | everything else | `review` |

Every recommendation carries the evidence that drove it. Recommendations are deterministic given the same artifact window.

## Auto-Apply Guardrails

### Family allowlist

Only three families are auto-appliable in the first version:
- `observation_coverage`
- `test_visibility`
- `dependency_drift`

Gated families (`hotspot_concentration`, `todo_accumulation`, `execution_health_followup`) require manual promotion.

### Key allowlist

Only `min_consecutive_runs` can be modified automatically.

### Range limits

`min_consecutive_runs` is bounded to `[1, 5]`. Any suggestion outside this range is skipped with reason `outside_range`.

### Step size

Maximum ±1 per family per application. No multi-step jumps.

### Per-family cooldown

Default: 48 hours. No change to the same family within this window.
Override: `CONTROL_PLANE_TUNING_FAMILY_COOLDOWN_HOURS`

### Daily quota

Default: 2 changes total per day across all families.
Override: `CONTROL_PLANE_TUNING_MAX_CHANGES_PER_DAY`

### Oscillation prevention

If a family was changed in one direction within the cooldown window, the opposite direction is blocked.

### Sample floor

Auto-apply is skipped if `sample_runs < 5` (same floor as recommendations).

## Retained Artifacts

Every tuning run writes four files under `tools/report/control_plane/tuning/<run_id>/`:

| File | Contents |
|------|----------|
| `tuning_run.json` | Complete artifact (loaded by cooldown/quota checks) |
| `family_tuning_summary.json` | Per-family metrics for the analysis window |
| `tuning_recommendations.json` | One recommendation per family with evidence |
| `tuning_changes.json` | Applied and skipped changes with before/after values |

Every applied change records: `family`, `key`, `before`, `after`, `reason`, `applied_at`.
Every skipped change records: `family`, `intended_action`, `reason`, `evidence`.

## Tuning Config File

Auto-apply writes to `config/autonomy_tuning.json`:

```json
{
  "version": 1,
  "updated_at": "2026-04-04T12:00:00Z",
  "overrides": {
    "observation_coverage": {"min_consecutive_runs": 1},
    "test_visibility": {"min_consecutive_runs": 4}
  }
}
```

This file is gitignored (or should be treated as runtime state). To revert a tuning change, edit or delete the file. The `DecisionEngineService` falls back to hardcoded defaults if the file is absent.

## Cadence

The regulator is not wired into the hot-path autonomy cycle. Run it as a periodic maintenance step:

```bash
# Weekly during the first month; monthly thereafter
./scripts/control-plane.sh tune-autonomy
```

Or add it to a cron/maintenance schedule. It should **not** run on every `autonomy-cycle` call.

## What This Is Not

- Not open-ended self-modification
- Not code generation for autonomy logic
- Not automatic addition of new candidate families
- Not unrestricted config editing
- Not a replacement for operator review of major policy shifts

Major changes (promoting a gated family, changing the auto-apply allowlist, adjusting the daily quota) remain operator decisions.
