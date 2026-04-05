# Autonomy Threshold Tuning Guide

This guide explains how to interpret `analyze-artifacts` output and tune the repo-aware autonomy loop over time.

## Two Tuning Tools

Control Plane provides two complementary tools for understanding and adjusting autonomy behavior.

### `analyze-artifacts` — per-family inspection

```bash
./scripts/control-plane.sh analyze-artifacts
./scripts/control-plane.sh analyze-artifacts --repo ControlPlane
./scripts/control-plane.sh analyze-artifacts --repo ControlPlane --limit 20
```

Reads retained decision and proposer artifacts and prints a per-family table with recommendations. Best for quick human inspection.

```
family                  emitted  suppressed  created  guardrail_skipped  suppress_rate
observation_coverage         4           2        3                  0         33%
test_visibility              1           3        1                  0         75%
dependency_drift             2           1        2                  0         33%
```

Flags:
- `suppress_rate >= 90%` → consider loosening threshold
- `emitted > 0 but created == 0` → check guardrails or proposer dedup
- `guardrail_skipped > 0` → proposals blocked by budget or cooldown

### `tune-autonomy` — bounded self-tuning regulation loop

```bash
# Recommendation-only (default, safe, writes artifacts but no config changes)
./scripts/control-plane.sh tune-autonomy

# With wider window
./scripts/control-plane.sh tune-autonomy --window 30

# Auto-apply mode (opt-in, requires env var as second gate)
CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1 ./scripts/control-plane.sh tune-autonomy --apply
```

The regulation loop:

1. Aggregates per-family metrics from retained decision + proposer artifacts.
2. Applies explicit recommendation rules (over-suppressed → loosen; noisy/low-value → tighten; healthy → keep).
3. In auto-apply mode, applies conservative bounded changes to `config/autonomy_tuning.json`.
4. Retains a full audit trail under `tools/report/control_plane/tuning/<run_id>/`.

The `DecisionEngineService` reads `config/autonomy_tuning.json` at startup if it exists, applying overrides to rule thresholds. To revert a change, delete or edit the file.

**Retained artifacts per run:**
- `family_tuning_summary.json` — per-family metrics
- `tuning_recommendations.json` — one recommendation per family with evidence
- `tuning_changes.json` — applied and skipped changes with before/after values
- `tuning_run.json` — combined artifact used by cooldown/quota checks

## Cadence

Run `tune-autonomy` as a periodic maintenance step, not on every autonomy cycle:

- **Weekly** during the first month of deployment
- **Monthly** once behavior stabilizes
- **After any significant threshold change** to validate the change had the intended effect
- **After promoting a new candidate family** to confirm it's behaving well

## The Manual Tuning Loop

For hands-on adjustments (or for families not in the auto-apply allowlist):

```
observe-repo (daily) -> generate-insights -> decide-proposals -> propose-from-candidates
                                      ↓
                    tune-autonomy (weekly)  <- review recommendations
                                      ↓
                     manually edit thresholds or update tuning config
                                      ↓
                    autonomy-cycle --dry-run  <- verify output looks right
                                      ↓
                    autonomy-cycle --execute  <- go live
```

## Default vs Gated Families

Families in `_DEFAULT_ALLOWED_FAMILIES` fire automatically on every cycle:

| Family | Active by default |
|--------|-------------------|
| `observation_coverage` | yes |
| `test_visibility` | yes |
| `dependency_drift` | yes |
| `execution_health_followup` | yes |
| `hotspot_concentration` | no — requires `--all-families` |
| `todo_accumulation` | no — requires `--all-families` |

## Per-Family Threshold Reference

### `execution_health_followup`

Fires when retained execution artifacts show systemic execution quality problems.

Two patterns:

**`high_no_op_rate`**
- **Condition**: `no_op_count / total_runs >= 0.5` and `total_runs >= 5`
- **When to loosen**: too many spurious proposals for repos that legitimately have many no-op test runs; raise the rate threshold to 0.65 or raise `_MIN_RUNS_FOR_RATE`.
- **When to tighten**: lower the threshold if you want earlier warning; e.g. 0.4 on repos where no-ops are reliably a signal of bad task quality.
- **Where to change**: `src/control_plane/insights/derivers/execution_health.py` constants `_HIGH_NO_OP_RATE_THRESHOLD`, `_MIN_RUNS_FOR_RATE`.

**`persistent_validation_failures`**
- **Condition**: `validation_failed_count >= 3`
- **When to loosen**: repos under active development naturally have transient failures; raise threshold to 5.
- **When to tighten**: lower to 2 for repos with strict quality gates where even 2 failures warrant a task.
- **Where to change**: `_VALIDATION_FAILURE_THRESHOLD` in the same file.

### `observation_coverage`

Fires when a repo signal (e.g. test signal) has been persistently unavailable across snapshots.

- **Rule**: `ObservationCoverageRule(min_consecutive_runs=2)`
- **When to loosen**: High suppress_rate from `cooldown_active`; the signal clears and re-appears frequently.
- **When to tighten**: Tasks are created but the signal resolves without action — raise `min_consecutive_runs` to 3 or 4.
- **Conservative default**: 2 consecutive runs. Appropriate for early deployment.

### `test_visibility`

Fires when test status has been persistently unknown.

- **Rule**: `TestVisibilityRule(min_consecutive_runs=3)`
- **When to loosen**: Suppress_rate is high because the signal flickers; lower to 2.
- **When to tighten**: Tasks are created too frequently for transient test signal loss; raise to 4.
- **Conservative default**: 3 consecutive runs. Higher bar than observation_coverage intentionally.

### `dependency_drift`

Fires when dependency drift is persistently detected.

- **Rule**: `DependencyDriftRule(min_consecutive_runs=2)`
- **When to loosen**: Drift is always present (expected in active repos); raise threshold or add a pattern exclusion.
- **When to tighten**: Not usually needed; drift is a slow-moving signal.
- **Conservative default**: 2 consecutive runs.

### `hotspot_concentration` (gated, not default)

Fires when a file appears repeatedly in file-hotspot snapshots.

- **Status**: In `ALL_FAMILIES` but not in `_DEFAULT_ALLOWED_FAMILIES`. Enable via `--all-families` in `autonomy-cycle` or by adding to `allowed_families` in `DecisionContext`.
- **Promotion criteria**: Enable only when you've confirmed that hotspot signals reliably identify files worth decomposing (not just frequently edited files that are intentionally central).

### `todo_accumulation` (gated, not default)

Fires when TODO concentration is high.

- **Status**: Same gating as `hotspot_concentration`.
- **Promotion criteria**: Enable when you've reviewed several TODO signals and confirmed they represent real technical debt, not intentional markers.

## Cooldown and Quota Tuning

The policy-level controls (applied across all families) are:

- **`cooldown_minutes`** — how long after a candidate was last emitted before it can be emitted again for the same dedup key. Default: 120 minutes.
- **`max_candidates`** — max candidates per decision run. Default: 3.
- **`max_candidates_per_family`** — max 1 per family per run (enforced in policy).

To adjust these for a specific run:

```bash
# Watcher uses these defaults; override in DecisionContext when calling directly
./scripts/control-plane.sh decide-proposals --max-candidates 5 --cooldown-minutes 60
```

Or update the watcher defaults in the worker `main.py` constants if you want permanent changes:

```python
PROPOSAL_COOLDOWN_SECONDS = 20 * 60   # 20 minutes
MAX_PROPOSALS_PER_CYCLE = 4
MAX_PROPOSALS_PER_DAY = 30
```

## When to Run the Tuning Loop

- **Weekly** during the first month of deployment.
- **After any watcher restart** that caused rate-limited runs or budget exhaustion — check if `remaining_exec_capacity` suppression dominated the output.
- **After promoting a new family** — confirm the new family's emit/create ratio is healthy before leaving it enabled permanently.
- **After a burst of autonomy tasks** — confirm the burst was from real signals, not threshold drift.

## Reading Suppression Reasons

| Reason | Meaning | Action |
|--------|---------|--------|
| `cooldown_active` | Same dedup key was emitted recently | Normal; wait for cooldown to clear |
| `quota_exceeded` | `max_candidates` or `max_candidates_per_family` hit | Raise limits if signal quality is high |
| `family_deferred_initial_gating` | Family not in `allowed_families` | Promote the family when ready |
| `proposal_budget_too_low` | Execution budget too low for proposals | Check `usage.json`; budget resets hourly/daily |
| `existing_open_equivalent_task` | Board already has an open task with this dedup key | Expected; no action needed |

## Documenting Threshold Changes

When you change a threshold, add a comment inline or update this file with the before/after:

```
# 2026-04-04: raised TestVisibilityRule min_consecutive_runs from 3 to 4
# Reason: test signal flickered on code_youtube_shorts 3 times in 2 days,
# creating tasks that resolved themselves before kodo could run them.
```

This audit trail is how you distinguish "threshold correctly tuned" from "threshold silently drifted."
