# Managed Repo Mini Regression Suite

## Purpose

Phase 11 turns individual Phase 10 slice replays into a compact, repeatable mini regression suite.

A mini regression suite lets you run multiple local replay checks across curated fixture packs and produce one suite-level result — without running a full audit or re-harvesting fixtures.

Mini regression is:
- Small and bounded (a fixed list of entries from a JSON definition file)
- Local and deterministic (reads only from fixture pack directories)
- Composable (entries combine different packs and profiles)
- Non-mutating (never modifies fixture packs, manifests, or source artifacts)

Mini regression is not:
- A full audit run
- A fixture harvesting step
- A Phase 6 dispatch call
- A calibration or recommendation mechanism

---

## Relationship to Phase 10 Slice Replay

Phase 11 is a thin orchestrator over Phase 10. Each suite entry maps 1:1 to one `SliceReplayRequest`. The suite runner calls `run_slice_replay()` for each entry, collects the results, and assembles a `MiniRegressionSuiteReport`.

```
Suite Definition (JSON)
    ↓
Phase 11: run_mini_regression_suite(request) → MiniRegressionSuiteReport
    ↓ (per entry)
Phase 10: run_slice_replay(request) → SliceReplayReport
    ↓ (per entry)
Phase 9: load_fixture_pack() → FixturePack
```

---

## Suite Definition

A suite definition is a JSON file that lists entries in order:

```json
{
  "schema_version": "1.0",
  "suite_id": "basic_fixture_integrity",
  "name": "Basic Fixture Integrity Suite",
  "description": "...",
  "created_at": "2026-04-26T00:00:00Z",
  "entries": [
    {
      "entry_id": "representative_run999_integrity",
      "fixture_pack_path": "tools/audit/fixtures/representative__Bucket_run999__...",
      "replay_profile": "fixture_integrity",
      "required": true,
      "fail_fast": false
    }
  ]
}
```

`entry_id` values must be unique within a suite. Duplicate IDs are rejected at load time.

---

## Suite Entry Fields

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `entry_id` | `str` | — | Unique within the suite; path-safe |
| `fixture_pack_path` | `str` | — | Path to `fixture_pack.json` or pack directory |
| `replay_profile` | `SliceReplayProfile` | — | Must be explicit |
| `required` | `bool` | `true` | If true, failure causes suite to fail/error |
| `selected_fixture_artifact_ids` | `list[str] \| null` | `null` | Restrict replay to specific artifact IDs |
| `source_stage` | `str \| null` | `null` | Stage filter (STAGE_SLICE profile) |
| `artifact_kind` | `str \| null` | `null` | Kind filter |
| `max_artifact_bytes` | `int` | 10 MiB | Content read size limit |
| `fail_fast` | `bool` | `false` | Stop this entry after first check failure |
| `metadata` | `dict` | `{}` | Pass-through |

---

## Suite Run Request

`MiniRegressionRunRequest` is a plain dataclass (non-serializable):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `suite_definition` | `MiniRegressionSuiteDefinition` | — | Loaded suite |
| `output_dir` | `Path` | — | Root for all output |
| `fail_fast` | `bool` | `False` | Stop suite after first required entry failure/error |
| `include_optional_entries` | `bool` | `True` | If False, optional entries are skipped |
| `run_id` | `str` | auto UUID4 | Override for deterministic naming |
| `metadata` | `dict` | `{}` | Pass-through to report |

---

## Suite Status Rules

| Suite Status | Condition |
|---|---|
| `passed` | All required entries passed; optional entries passed or skipped |
| `failed` | ≥1 required entry has status `failed` |
| `error` | ≥1 required entry has status `error` (and none `failed`) |
| `partial` | Stopped early via `fail_fast` after some entries completed |

Optional entry failures **never** cause the suite to fail. They appear in the report summary as `optional_failures`.

---

## Entry Result Model

`MiniRegressionEntryResult` (frozen Pydantic):

| Field | Type | Notes |
|-------|------|-------|
| `entry_id` | `str` | |
| `fixture_pack_id` | `str` | Source pack ID from SliceReplayReport |
| `fixture_pack_path` | `str` | |
| `replay_profile` | `SliceReplayProfile` | |
| `required` | `bool` | |
| `status` | `"passed" \| "failed" \| "error" \| "skipped"` | |
| `slice_replay_report_path` | `str` | Path to the per-entry SliceReplayReport; empty on error |
| `summary` | `str` | One-line description from SliceReplayReport |
| `error` | `str` | Set when the runner raised an exception |
| `metadata` | `dict` | |

---

## Suite Report Model

`MiniRegressionSuiteReport` (Pydantic, serializable to JSON):

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | `str` | `"1.0"` |
| `suite_run_id` | `str` | Auto-generated path-safe ID |
| `suite_id` | `str` | From suite definition |
| `suite_name` | `str` | |
| `created_at` | `datetime` | UTC |
| `started_at` | `datetime` | Before first entry runs |
| `ended_at` | `datetime` | After last entry completes |
| `status` | `SuiteStatus` | Overall suite status |
| `entry_results` | `list[MiniRegressionEntryResult]` | One per attempted entry |
| `summary` | `MiniRegressionSuiteSummary` | Aggregated counts |
| `report_paths` | `list[str]` | Paths to per-entry SliceReplayReport files |
| `limitations` | `list[str]` | |
| `metadata` | `dict` | |

Report path: `{output_dir}/{suite_id}/{suite_run_id}/suite_report.json`

Per-entry replay reports: `{output_dir}/_replay/{suite_run_id}/{repo_id}/{pack_id}/{replay_id}.json`

---

## Suite Summary

`MiniRegressionSuiteSummary` (frozen Pydantic):

| Field | Notes |
|-------|-------|
| `total_entries` | All entries attempted (not skipped before run) |
| `required_entries` | Count of required entries in results |
| `optional_entries` | Count of optional entries in results |
| `passed_entries` | Entries with status `passed` |
| `failed_entries` | Entries with status `failed` |
| `error_entries` | Entries with status `error` |
| `skipped_entries` | Entries with status `skipped` |
| `required_failures` | Required entries with `failed` or `error` |
| `optional_failures` | Optional entries with `failed` or `error` |

`.text` property: `"3 entries: 2 passed, 1 failed, 0 error, 0 skipped (1 required failures)"`

---

## Fail-Fast Behavior

Two levels of fail-fast exist:

1. **Entry-level** (`MiniRegressionSuiteEntry.fail_fast`): passed through to `SliceReplayRequest`, stops check execution within that one entry after the first required check failure.

2. **Suite-level** (`MiniRegressionRunRequest.fail_fast`): stops iterating suite entries after the first required entry that results in `failed` or `error`. The suite status becomes `partial`.

---

## CLI / Tool Entry Points

```bash
# Run a suite
operations-center-regression run \
    --suite examples/mini_regression/basic_fixture_integrity_suite.json \
    --output-dir tools/audit/report/mini_regression

# Run with fail-fast and skip optional entries
operations-center-regression run \
    --suite examples/mini_regression/failure_slice_suite.json \
    --fail-fast \
    --skip-optional

# Inspect a previously written suite report
operations-center-regression inspect \
    --report tools/audit/report/mini_regression/basic_fixture_integrity/suite_20260426_120000/suite_report.json

# List entries in a suite definition
operations-center-regression list \
    --suite examples/mini_regression/basic_fixture_integrity_suite.json
```

Exit codes:
- `0` — suite passed or partial (no failure)
- `1` — suite failed or error
- `2` — suite definition error
- `3` — infrastructure error (cannot write report)

---

## Non-Goals

Phase 11 explicitly does **not**:

- Run full audits
- Call Phase 6 dispatch
- Harvest new fixtures
- Modify fixture packs, source artifacts, or manifests
- Apply calibration recommendations
- Import managed repo code
- Reproduce producer failures
- Scan outside fixture pack directories

---

## Module Layout

```
src/operations_center/mini_regression/
    __init__.py      — public exports
    errors.py        — MiniRegressionError hierarchy
    models.py        — MiniRegressionSuiteEntry, MiniRegressionSuiteDefinition,
                       MiniRegressionEntryResult, MiniRegressionSuiteSummary,
                       MiniRegressionSuiteReport, MiniRegressionRunRequest
    suite_loader.py  — load_mini_regression_suite()
    runner.py        — run_mini_regression_suite()
    reports.py       — write_suite_report(), load_suite_report()

src/operations_center/entrypoints/regression/
    main.py          — operations-center-regression CLI (Typer)

examples/mini_regression/
    basic_fixture_integrity_suite.json
    failure_slice_suite.json
```
