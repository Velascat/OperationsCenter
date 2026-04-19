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

| Family | Active by default | Default tier | Risk class |
|--------|-------------------|-------------|------------|
| `observation_coverage` | yes | 1 | logic |
| `test_visibility` | yes | 1 | logic |
| `dependency_drift_followup` | yes | 1 | logic |
| `execution_health_followup` | yes | 1 | logic |
| `lint_fix` | yes | 2 | style |
| `type_fix` | yes | 1 | logic |
| `validation_pattern_followup` | yes | 1 | logic |
| `ci_pattern` | no — requires `--all-families` | 1 | logic |
| `hotspot_concentration` | no — requires `--all-families` | 1 | structural |
| `todo_accumulation` | no — requires `--all-families` | 1 | style |
| `backlog_promotion` | no — requires `--all-families` | 1 | logic |
| `arch_promotion` | no — requires `--all-families` + health gates | 0 | arch |

## Autonomy Tier Management

Autonomy tiers control the initial Plane task state for created tasks. Tier 2 tasks auto-execute; tier 1 tasks land in Backlog and require human promotion; tier 0 tasks are never created.

```bash
# View current tiers
./scripts/control-plane.sh autonomy-tiers show

# Promote a family to auto-execute after confirming track record
./scripts/control-plane.sh autonomy-tiers set --family lint_fix --tier 2

# Demote a family after a bad run
./scripts/control-plane.sh autonomy-tiers set --family type_fix --tier 0
```

**When to promote a family to tier 2:**
- `tune-autonomy` shows `acceptance_rate >= 80%` with ≥ 5 feedback records
- No runaway board spam from this family in the last 30 days
- Human review of 3-5 created tasks confirms the scope is consistently bounded

**When to demote a family:**
- `acceptance_rate < 30%` across 5+ feedback records — proposals are not landing
- Tasks are consistently escalated rather than merged
- `tune-autonomy` recommends `tighten_threshold` with `autonomy_tier: decrease`

## Acceptance Rate Tuning

The self-tuning regulator now tracks `proposals_merged` and `proposals_escalated` per family by joining feedback records to proposer artifacts. The resulting `acceptance_rate = merged / (merged + escalated)`.

Two new recommendation rules fire based on acceptance rate:

| Pattern | Condition | Action |
|---------|-----------|--------|
| Low acceptance | acceptance_rate < 30% AND ≥ 5 feedback records | `tighten_threshold` — suggests decreasing autonomy tier |
| High acceptance | acceptance_rate ≥ 80% AND ≥ 5 feedback records | `keep` — suggests increasing autonomy tier |

These suggestions appear in `tuning_recommendations.json` as `suggested_change: {"autonomy_tier": {"direction": "increase|decrease", "step": 1}}`. They are advisory — the operator applies them manually via `autonomy-tiers set`.

The acceptance rate metrics appear in the `tune-autonomy` output per family. To collect meaningful data, ensure the reviewer watcher is writing feedback records (it does so automatically on merge/escalate), and use the `feedback` entrypoint for tasks handled manually.

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

### `dependency_drift_followup`

Fires when dependency drift is persistently detected.

- **Rule**: `DependencyDriftRule(min_consecutive_runs=2)`
- **When to loosen**: Drift is always present (expected in active repos); raise threshold or add a pattern exclusion.
- **When to tighten**: Not usually needed; drift is a slow-moving signal.
- **Conservative default**: 2 consecutive runs.

### `lint_fix`

Fires when ruff detects lint violations.

- **Rule**: `LintDriftRule` — fires on `lint_drift/present` or `lint_drift/worsened` insights
- **Default tier**: 2 (auto-executes) — style risk class, bounded scope
- **When to demote to tier 1**: repos where lint fixes have historically caused unintended refactors; demote to tier 1 so a human reviews before execution
- **Where to change**: `src/control_plane/insights/derivers/lint_drift.py`

### `type_fix`

Fires when `ty` or `mypy` reports type errors.

- **Rule**: `TypeImprovementRule(min_errors=3)` — requires ≥3 errors before firing
- **Default tier**: 1 — logic risk class; requires human review before execution
- **When to loosen**: raise tier to 2 after confirming that auto-generated type fixes are consistently bounded and safe in your codebase
- **When to tighten**: lower `min_errors` threshold to 1 if you want earlier warning; raise to 10 if noise is high
- **Where to change**: `src/control_plane/decision/rules/type_improvement.py` constant `min_errors`

### `validation_pattern_followup`

Fires when the same Plane task has ≥2 runs and ≥2 validation failures across retained execution artifacts.

- **Rule**: `ValidationPatternRule` — high confidence if ≥3 affected tasks; medium confidence otherwise
- **Default tier**: 1 — logic risk class; investigation required before executing
- **When to loosen**: lower `_MIN_FAILURES_FOR_PATTERN` to 1 if you want earlier warning
- **When to tighten**: raise `_MIN_RUNS_FOR_PATTERN` to 3 if transient failures are common
- **Where to change**: `src/control_plane/observer/collectors/validation_history.py`

### `ci_pattern` (gated, not default)

Fires when GitHub check-run history shows failing or flaky checks.

- **Rule**: `CIPatternRule` — `checks_failing` (confidence=high) or `checks_flaky` (confidence=medium)
- **Default tier**: 1 — logic risk class; root cause investigation required
- **Promotion criteria**: enable once you have ≥2 weeks of CI history baseline and have confirmed that the failing/flaky classification is reliable for your repo
- **Thresholds**: `FAILING_THRESHOLD=0.7` (≥70% fail rate), `FLAKY_THRESHOLD=0.2` (≥20%)
- **Where to change**: `src/control_plane/observer/collectors/ci_history.py`

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
| `velocity_cap_exceeded` | ≥10 proposals created in the last 24 hours | Wait for the window to pass; or raise `max_proposals_per_24h` |
| `proposal_stale_open` | Prior unresolved proposal exceeded `expires_after_runs` without feedback | Close or record feedback for the old task; or increase `expires_after_runs` |

## Calibration Time Decay

The `ConfidenceCalibrationStore` filters events by recency when computing acceptance rates. The default window is 90 days.

```bash
# View calibration with the default 90-day window
./scripts/control-plane.sh tune-autonomy

# Widen the window to see all historical data
./scripts/control-plane.sh tune-autonomy --window 180
```

The `window_days` parameter is passed to `calibration_for()` and `report()` internally. Events older than the window are excluded from acceptance-rate calculations.

**Cleaning up stale events:**

```python
from control_plane.tuning.calibration import ConfidenceCalibrationStore
store = ConfidenceCalibrationStore()
store.cleanup_old_events(window_days=90)  # removes events older than 90 days
```

This is safe to run periodically. It does not affect the recommendation output — recommendations already exclude old events via the window — but it keeps `state/calibration_store.json` compact.

**Why time decay matters:** Early feedback records may reflect a different codebase state or an earlier version of Kodo. Excluding stale events prevents historical over-confidence from blocking proposals that are now reliably accepted.

## Proposal Utility Scoring

Before the per-cycle proposal cap is applied, proposals are ranked by a utility score:

```
score = confidence_weight + calibration_bonus + state_bonus - scope_penalty
```

- `confidence_weight`: 1.0 for high, 0.6 for medium, 0.2 for low
- `calibration_bonus`: up to +0.3 based on the family's recent acceptance rate (0.0 when no calibration data)
- `state_bonus`: +0.1 if the proposal targets a task already in Backlog
- `scope_penalty`: -0.2 for medium complexity (3–7 files), -0.5 for high complexity (≥8 files)

High-complexity proposals (≥8 files affected) are automatically placed in Backlog regardless of score. This prevents unachievable-scope proposals from consuming the execution budget.

To inspect current proposal scores for a cycle, add `--dry-run` and check the retained `proposal_candidates.json` artifact — the `utility_score` field is written alongside each candidate.

## Documenting Threshold Changes

When you change a threshold, add a comment inline or update this file with the before/after:

```
# 2026-04-04: raised TestVisibilityRule min_consecutive_runs from 3 to 4
# Reason: test signal flickered on VideoFoundry 3 times in 2 days,
# creating tasks that resolved themselves before kodo could run them.
```

This audit trail is how you distinguish "threshold correctly tuned" from "threshold silently drifted."
