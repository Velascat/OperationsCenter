# Repo Observer

The observer is the first stage of the autonomy pipeline. It inspects a local repo without modifying it, collects a bounded set of factual signals, and emits one normalized snapshot artifact per run.

It does **not**:

- score or rank work
- create Plane tasks
- decide what should happen next
- modify the observed repo

## Role In The Larger Autonomy Flow

```text
observe -> analyze -> decide -> propose
```

## Signals Collected

| Signal | Collector | Description |
|--------|-----------|-------------|
| git branch context | `GitContextCollector` | current branch, remote URL, uncommitted changes |
| recent commits | `RecentCommitsCollector` | last N commit summaries |
| file hotspots | `FileHotspotsCollector` | files most frequently changed in recent history |
| test signal | `TestSignalCollector` | last known local test status from retained artifacts |
| dependency drift | `DependencyDriftCollector` | drift signal from retained dependency-check artifacts |
| TODO/FIXME summary | `TodoSignalCollector` | count and sample of TODO/FIXME markers |
| execution health | `ExecutionArtifactCollector` | outcome rates and validation failure counts from retained kodo_plane artifacts |
| lint signal | `LintSignalCollector` | lint violation count and status from `ruff check` |
| type signal | `TypeSignalCollector` | type error count and status from `ty` or `mypy` |
| CI history | `CIHistoryCollector` | failing and flaky checks from GitHub check-run history |
| validation history | `ValidationHistoryCollector` | per-task validation failure patterns from retained execution artifacts |

## Collector Details

### ExecutionArtifactCollector

Reads retained run artifacts from `tools/report/kodo_plane/` and computes for the target repo:

- `total_runs` — number of retained artifact directories matched to this repo
- `executed_count` / `no_op_count` — breakdown of outcome status
- `validation_failed_count` — how many executed runs failed post-execution validation
- `recent_runs` — the ten most recent `ExecutionRunRecord` objects for audit trail

### LintSignalCollector

Runs `ruff check --output-format=json` in the observed repo and produces:

- `status` — `clean`, `violations`, or `unavailable`
- `violation_count` — total lint violations found
- `distinct_file_count` — true count of distinct files with violations (computed from full output, not sampled top-N)
- `top_violations` — up to 20 highest-priority violations
- `source` — `"ruff"`

Falls back to `status=unavailable` if ruff is not installed or the run times out.

### TypeSignalCollector

Tries `ty check --output-format json` first, falls back to `mypy --output=json`. Produces:

- `status` — `clean`, `errors`, or `unavailable`
- `error_count` — number of type errors found
- `distinct_file_count` — true count of distinct files with errors (computed from full output, not sampled top-N)
- `top_errors` — up to 20 highest-priority errors
- `source` — `"ty"` or `"mypy"`

Falls back to `status=unavailable` if neither tool is installed or the run times out.

### CIHistoryCollector

Reads GitHub check-run history for the last 5 commit SHAs via the GitHub API. Requires `repo_git_token` in settings and `clone_url` to derive the GitHub owner/repo.

Classifies checks as:

- **failing** — ≥70% fail rate across sampled SHAs
- **flaky** — ≥20% fail rate (but below the failing threshold)

Produces `CIHistorySignal` with `status` (`nominal`, `flaky`, `failing`, `unavailable`), `failing_checks`, `flaky_checks`, and `failure_rate`.

Falls back to `status=unavailable` if the token is absent or the API request fails.

### ValidationHistoryCollector

Reads retained execution artifacts grouped by `task_id` and surfaces per-task validation failure patterns:

- Groups artifacts by `task_id` from `control_outcome.json`, `request.json`, and `validation.json`
- Filters to artifacts for the observed repo
- Flags tasks where `total_runs >= 2` AND `validation_failure_count >= 2` as repeated failure patterns
- Returns `tasks_with_repeated_failures` sorted by failure count descending

This is distinct from `ExecutionArtifactCollector`, which only aggregates overall rates. The validation history collector identifies specific tasks that repeatedly fail validation.

## Output

Observer runs write retained artifacts under:

- `tools/report/control_plane/observer/<run_id>/repo_state_snapshot.json`
- `tools/report/control_plane/observer/<run_id>/repo_state_snapshot.md`

The JSON snapshot is the primary machine-consumable contract for later passes.

## Guardrails

- read-only against the observed repo
- bounded signal collection
- all network-dependent collectors (CIHistoryCollector) fall back to `unavailable` on failure
- partial collector failures are recorded in `collector_errors` without aborting the snapshot
