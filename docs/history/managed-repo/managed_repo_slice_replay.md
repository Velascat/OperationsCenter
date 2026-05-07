# Managed Repo Slice Replay Testing

## Purpose

Phase 10 turns harvested fixture packs into executable, focused replay tests.
A replay test validates a narrow behavior slice against previously captured real-run
artifact data, without rerunning the full managed audit.

Replay is:
- Small and bounded (one fixture pack, one profile)
- Local and deterministic (reads from fixture pack directory only)
- Provenance-preserving (records which pack and which checks ran)

Replay is not:
- A full audit run
- A regression suite
- A producer integration test
- A mutation mechanism

---

## Relationship to Phase 9 Fixture Harvesting

Phase 10 is a consumer of Phase 9. It loads fixture packs using the Phase 9
`load_fixture_pack()` loader — the only path to fixture data. It never calls
`harvest_fixtures()` or creates new fixture packs.

```
Phase 9: FixturePack (on disk)
    ↓
Phase 10: run_slice_replay(request) → SliceReplayReport
```

---

## Replay Profiles

| Profile | What it checks |
|---------|---------------|
| `fixture_integrity` | Pack structure: fixture_pack.json loads, source_manifest.json and source_index_summary.json present, copied files exist, checksums match if available |
| `manifest_contract` | Source manifest and index summary are present and parse as valid JSON |
| `artifact_readability` | Copied JSON/text artifacts are readable within size limits |
| `failure_slice` | Pack carries failure/partial limitations; metadata-only entries have reasons; manifest still readable |
| `stage_slice` | Artifacts for a specific `source_stage` exist, match the stage, and are readable |
| `metadata_only_slice` | Metadata-only (copied=False) entries all carry a `copy_error` or limitation |

---

## Replay Request Model

`SliceReplayRequest` is a plain dataclass:

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `fixture_pack_path` | `Path` | — | Path to `fixture_pack.json` or pack directory |
| `replay_profile` | `SliceReplayProfile` | — | Must be explicit |
| `selected_fixture_artifact_ids` | `list[str] \| None` | `None` | Restrict replay to specific artifact IDs |
| `source_stage` | `str \| None` | `None` | Filter artifacts by stage (for STAGE_SLICE) |
| `artifact_kind` | `str \| None` | `None` | Filter artifacts by kind |
| `max_artifact_bytes` | `int` | 10 MiB | Size limit for content reads |
| `fail_fast` | `bool` | `False` | Stop after first required check failure |
| `metadata` | `dict` | `{}` | Pass-through |

---

## Replay Check Model

`SliceReplayCheck` (frozen Pydantic) describes a single check:

| Field | Type | Notes |
|-------|------|-------|
| `check_id` | `str` | Auto UUID4 |
| `check_type` | `str` | Key in `CHECK_REGISTRY` |
| `fixture_artifact_ids` | `list[str]` | Artifact IDs this check applies to |
| `description` | `str` | Human-readable description |
| `required` | `bool` | If True, failure causes `status="failed"` in report |
| `metadata` | `dict` | Pass-through |

### Check types

| Check type | What it validates |
|------------|------------------|
| `fixture_pack_loads` | Pack was loaded (always passes if runner reaches this point) |
| `copied_file_exists` | Copied artifact file is present in `artifacts/` directory |
| `metadata_only_reason_present` | `copied=False` artifact has `copy_error` or limitation |
| `source_manifest_loads` | `source_manifest.json` is valid JSON with expected fields |
| `source_index_summary_loads` | `source_index_summary.json` is valid JSON with `total_artifacts` |
| `json_artifact_reads` | Copied JSON artifact parses as valid JSON within `max_artifact_bytes` |
| `text_artifact_reads` | Copied text artifact decodes as UTF-8 within `max_artifact_bytes` |
| `failure_limitation_present` | Pack or artifact carries `partial_run`, `failed_run`, or similar limitation |
| `checksum_matches_if_available` | Computed SHA-256 matches recorded checksum (skipped if none recorded) |
| `artifact_kind_matches` | Artifact kind matches the request filter |
| `source_stage_matches` | Artifact `source_stage` matches the request filter |

---

## Replay Result Model

`SliceReplayCheckResult` (frozen Pydantic):

| Field | Type | Notes |
|-------|------|-------|
| `check_id` | `str` | Matches the `SliceReplayCheck.check_id` |
| `status` | `"passed" \| "failed" \| "skipped" \| "error"` | |
| `fixture_artifact_ids` | `list[str]` | |
| `summary` | `str` | One-line description of what happened |
| `detail` | `str` | Extended context |
| `error` | `str` | Infrastructure error message (status="error" only) |
| `metadata` | `dict` | |

Status semantics:
- `passed` — expected condition was met
- `failed` — expected condition was not met
- `skipped` — check intentionally not applicable (e.g. not a JSON artifact for a JSON check)
- `error` — replay infrastructure could not perform the check (e.g. file unreadable)

---

## Replay Report Model

`SliceReplayReport` (Pydantic, serializable to JSON):

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | `str` | `"1.0"` |
| `replay_id` | `str` | Auto UUID4 |
| `created_at` | `datetime` | UTC |
| `fixture_pack_id` | `str` | Source pack ID |
| `fixture_pack_path` | `str` | Path used to load the pack |
| `source_repo_id` | `str` | |
| `source_run_id` | `str` | |
| `source_audit_type` | `str` | |
| `replay_profile` | `SliceReplayProfile` | |
| `status` | `"passed" \| "failed" \| "error" \| "partial"` | |
| `summary` | `str` | `"N checks: M passed, K failed, ..."` |
| `check_results` | `list[SliceReplayCheckResult]` | |
| `limitations` | `list[str]` | Inherited from fixture pack |
| `metadata` | `dict` | |

Properties: `passed_count`, `failed_count`, `error_count`, `skipped_count`, `total_count`.

Report path: `{output_dir}/{repo_id}/{fixture_pack_id}/{replay_id}.json`

Report status:
- `passed` — all required checks passed (skipped checks are acceptable)
- `failed` — at least one required check failed
- `error` — at least one check hit an infrastructure error
- `partial` — all results are skipped or mix of skipped/passed with no failures

---

## Metadata-Only Fixture Behavior

Artifacts that were not copied (`copied=False`) are valid fixture entries.
They represent missing files, unresolved paths, oversized artifacts, or binary artifacts.

Replay behavior for metadata-only artifacts:
- `copied_file_exists` — skipped (not applicable)
- `metadata_only_reason_present` — checks `copy_error` or `limitations` is non-empty
- `json_artifact_reads` / `text_artifact_reads` — skipped (no file to read)
- `checksum_matches_if_available` — skipped (no file)
- `failure_limitation_present` — may pass if artifact has failure limitation

The `METADATA_ONLY_SLICE` profile focuses exclusively on metadata-only entries.

---

## Failure Slice Behavior

The `FAILURE_SLICE` profile validates captured failure evidence:
- `failure_limitation_present` (required) — pack or artifact must carry `partial_run` or similar
- `source_manifest_loads` (required) — provenance must be readable
- `metadata_only_reason_present` (required for metadata-only entries) — missing files explained
- `json_artifact_reads` (optional) — copied artifacts around the failure are readable

Failure slice replay does **not** reproduce the original producer failure.
It validates that the evidence captured at harvest time is intact and interpretable.

---

## CLI / Tool Entry Points

```bash
# Run fixture integrity profile
operations-center-replay run \
    --fixture-pack tools/audit/fixtures/{pack_id}/ \
    --profile fixture_integrity

# Run artifact readability profile
operations-center-replay run \
    --fixture-pack tools/audit/fixtures/{pack_id}/fixture_pack.json \
    --profile artifact_readability \
    --output-dir tools/audit/report/slice_replay

# Run stage slice with fail-fast
operations-center-replay run \
    --fixture-pack tools/audit/fixtures/{pack_id}/ \
    --profile stage_slice \
    --stage TopicSelectionStage \
    --fail-fast

# Inspect a previously written report
operations-center-replay inspect \
    --report tools/audit/report/slice_replay/{repo_id}/{pack_id}/{replay_id}.json
```

---

## Non-Goals

Phase 10 explicitly does **not**:

- Run full audits
- Call Phase 6 dispatch
- Harvest new fixtures
- Implement regression suite orchestration
- Mutate fixture packs or their `fixture_pack.json`
- Mutate source artifacts or manifests
- Apply calibration recommendations
- Import managed repo code
- Scan outside fixture pack directories
- Reproduce producer failures by running producer code

---

## Module Layout

```
src/operations_center/slice_replay/
    __init__.py     — public exports
    errors.py       — SliceReplayError hierarchy
    models.py       — SliceReplayProfile, SliceReplayRequest, SliceReplayCheck, SliceReplayCheckResult, SliceReplayReport
    checks.py       — 11 check implementations + CHECK_REGISTRY
    profiles.py     — profile → CheckSpec mapping, artifact filters
    runner.py       — run_slice_replay() (main entry point)
    reports.py      — write_replay_report(), load_replay_report()

src/operations_center/entrypoints/replay/
    main.py         — operations-center-replay CLI (Typer)
```
