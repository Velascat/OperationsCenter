# Repo-Aware Autonomy Layer

Operations Center's repo-aware autonomy layer is an internal pipeline that feeds bounded work into the existing proposer lane.

It is intentionally split into five explicit stages:

```text
observe -> analyze -> decide -> propose -> (execute+review -> feedback)
```

## Stage Artifacts

```text
tools/report/operations_center/observer/<run_id>/repo_state_snapshot.json
tools/report/operations_center/insights/<run_id>/repo_insights.json
tools/report/operations_center/decision/<run_id>/proposal_candidates.json
tools/report/operations_center/proposer/<run_id>/proposal_results.json
state/proposal_feedback/<task_id>.json          ← written by reviewer watcher or manually
```

Each stage reads the retained output of the previous stage instead of bypassing the artifact boundary.

## Purpose

This layer adds repo-aware initiative without changing the board-facing lifecycle contract.

- `goal`, `test`, `improve`, and `propose` remain the board-facing worker lanes.
- The autonomy layer becomes one more bounded source of proposer input.
- Plane remains the visible source of truth.

## Signals Collected

The observer collects eleven categories of factual signal:

| Signal | Source |
|--------|--------|
| git branch context | local git |
| recent commits | local git |
| file hotspots | local git history |
| test status | retained kodo artifacts |
| dependency drift | retained dependency-check artifacts |
| TODO/FIXME summary | local file scan |
| execution health | retained kodo_plane artifacts |
| lint violations | `ruff check` |
| type errors | `ty` or `mypy` |
| CI check history | GitHub API |
| per-task validation patterns | retained execution artifacts |

## Candidate Families

The decision engine currently supports twelve candidate families:

**Default (active on every cycle):**
`observation_coverage`, `test_visibility`, `dependency_drift_followup`, `execution_health_followup`, `lint_fix`, `type_fix`, `validation_pattern_followup`

**Gated (require `--all-families` or explicit promotion):**
`ci_pattern`, `hotspot_concentration`, `todo_accumulation`, `backlog_promotion`, `arch_promotion`

## Autonomy Tiers

Each family has an autonomy tier that controls the initial Plane task state:

| Tier | Task state | Effect |
|------|-----------|--------|
| 2 | `Ready for AI` | Executes on next watcher cycle |
| 1 | `Backlog` (logic/structural) or `Ready for AI` (style) | Human promotes before execution |
| 0 | Not created | Candidate visible in decision artifact only |

Tiers are configured via `config/autonomy_tiers.json` and managed with:

```bash
./scripts/operations-center.sh autonomy-tiers set --family lint_fix --tier 1
./scripts/operations-center.sh autonomy-tiers show
```

## Feedback Loop

The autonomy layer closes a feedback loop through the reviewer watcher and the self-tuning regulator:

```text
[Plane tasks execute] -> [kodo_plane artifacts written]
                                    ↓
                    [ExecutionArtifactCollector + ValidationHistoryCollector]
                                    ↓
              [ExecutionHealthDeriver, ValidationPatternDeriver: insights]
                                    ↓
              [ExecutionHealthRule, ValidationPatternRule: candidates → tasks]
                                    ↓
                    [Tasks merge or escalate via reviewer watcher]
                                    ↓
               [state/proposal_feedback/<task_id>.json written]
                                    ↓
          [tune-autonomy reads feedback → acceptance_rate per family]
                                    ↓
         [recommendation: tighten tier if low acceptance, keep/loosen if high]
```

Every autonomy-cycle run will detect execution quality regressions and propose bounded improve tasks. The feedback records from merged/escalated tasks flow back into the self-tuning regulator to calibrate the system's confidence in each candidate family over time.

## Cycle Report

Every `autonomy-cycle` run (whether dry-run or execute) writes a structured JSON report to `logs/autonomy_cycle/cycle_<ts>.json`. The report includes:

- Stage summaries (run IDs, signal collection status, insight counts, suppression breakdown)
- Full signal snapshot (test, lint, type, CI, validation, execution health)
- Guard rail summary (budget remaining, gated families, cycle health)
- `emitted_candidates` — list of `{family, validation_profile, confidence}` for every emitted candidate, enabling per-profile debugging of execution outcomes

## Boundaries

- `observe`, `analyze`, and `decide` are read-only against the observed repo.
- Only `propose` writes to Plane.
- No direct git writes or branch creation happen in this layer.
- Zero-candidate and zero-created runs are valid.
- Provenance is retained end to end.
- All network-dependent collectors (CI history) fall back to `unavailable` on failure — the pipeline continues.
