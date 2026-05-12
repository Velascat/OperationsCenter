# Managed Repo Fixture Harvesting

## Purpose

Phase 9 turns real managed audit runs into durable, reusable fixture packs.
A fixture pack is a structured slice of a real run — not a hand-made mock.
It captures selected artifacts, manifest metadata, calibration findings (as evidence),
and full provenance so that later phases can run fast slice replay and regression tests
without re-executing the full managed audit.

**Phase 9 does not execute replay tests.** That is Phase 10.

---

## Relationship to Phase 7 Artifact Index

Phase 9 is a consumer of Phase 7. It uses `ManagedArtifactIndex` as its source of
truth for artifact selection and path resolution. No directory scanning is performed.
Artifact selection is driven entirely by index metadata.

Phase 7 retrieval helpers (`resolve_artifact_path`, `read_text_artifact`) are used
to locate and copy artifact files where paths are resolvable and files exist on disk.

---

## Relationship to Phase 8 Calibration

Phase 9 optionally accepts `CalibrationFinding` objects to guide artifact selection.
Findings from a `BehaviorCalibrationReport` can be passed as `findings` in the
`HarvestRequest`. The harvester uses them to:

- Record which findings motivated the fixture (`FixtureFindingReference`)
- Restrict selection to artifacts referenced by specific finding IDs

Findings are **evidence only**. They are stored as `FixtureFindingReference` objects —
stripped of any prescription, retaining only provenance fields.

---

## Anti-Collapse Boundary

The anti-collapse invariant (established in Phase 8) applies in full:

```
artifact data → findings → recommendations → human decision → applied change
```

Phase 9 sits between `findings` and the fixture pack — it reads findings as selection
evidence, never as action instructions. Specifically:

- `CalibrationRecommendation` objects are never harvested into fixture packs
- Finding references have no `apply`, `execute`, or `mutate` fields
- Fixture packs have no `recommendations` or `actions` fields
- `requires_human_review` invariants remain enforced in the calibration layer

---

## Fixture Pack Model

`FixturePack` is a Pydantic model (serializable to JSON) stored as `fixture_pack.json`.

| Field | Type | Notes |
|-------|------|-------|
| `schema_version` | `str` | `"1.0"` |
| `fixture_pack_id` | `str` | Stable, path-safe ID |
| `created_at` | `datetime` | UTC |
| `created_by` | `str` | Default `"operations-center"` |
| `source_repo_id` | `str` | Source managed repo |
| `source_run_id` | `str` | Source audit run |
| `source_audit_type` | `str` | e.g. `"representative"` |
| `source_manifest_path` | `str` | Absolute path to artifact_manifest.json |
| `source_index_summary` | `ArtifactIndexSummary` | Quick stats for the full index |
| `harvest_profile` | `HarvestProfile` | Selection strategy used |
| `selection_rationale` | `str` | Optional human note |
| `artifacts` | `list[FixtureArtifact]` | Selected artifact entries |
| `findings` | `list[FixtureFindingReference]` | Optional finding provenance |
| `limitations` | `list[str]` | Inherited from source index |
| `metadata` | `dict` | Pass-through |

### fixture_pack_id format

```
{repo_id}__{run_id}__{profile}__{yyyymmdd_hhmmss}
```

All characters are path-safe (alphanumeric, underscore, hyphen). Special characters
in repo_id or run_id are replaced with underscores.

---

## Fixture Artifact Model

`FixtureArtifact` preserves full provenance to its `IndexedArtifact` source.

| Field | Type | Notes |
|-------|------|-------|
| `fixture_artifact_id` | `str` | Auto UUID4 |
| `source_artifact_id` | `str` | References `IndexedArtifact.artifact_id` |
| `artifact_kind` | `str` | From manifest |
| `source_stage` | `str\|None` | From manifest |
| `location` | `str` | `run_root`, `repo_singleton`, etc. |
| `path_role` | `str` | `primary`, `detail`, etc. |
| `source_path` | `str` | Original path from manifest |
| `fixture_relative_path` | `str\|None` | Relative to `artifacts/` dir; None if not copied |
| `content_type` | `str` | From manifest |
| `checksum` | `str\|None` | SHA-256 of copied file, or source checksum |
| `size_bytes` | `int\|None` | Actual file size |
| `copied` | `bool` | Whether the file was physically copied |
| `copy_error` | `str` | Reason for not copying (empty if copied) |
| `limitations` | `list[str]` | From source artifact |
| `metadata` | `dict` | Pass-through |

`copied=False` is valid and expected for:
- Missing files (status=missing)
- Unresolvable paths (no repo_root)
- Oversized artifacts
- Binary artifacts (unless explicitly allowed)
- Budget-exceeded artifacts

---

## Harvest Profiles

| Profile | Selection Strategy |
|---------|--------------------|
| `minimal_failure` | Missing + partial + missing-file artifacts — smallest set to inspect a failure |
| `partial_run` | Artifacts with `partial_run` limitation or `missing` status |
| `artifact_health` | Unresolved paths + missing files + partial artifacts |
| `producer_compliance` | All artifacts (for contract compliance review) |
| `stage_slice` | All artifacts for one `source_stage` (requires `source_stage` in request) |
| `full_manifest_snapshot` | All manifest artifacts |
| `manual_selection` | Explicitly provided `artifact_ids` (all must exist in index) |

All profiles are deterministic. The profile is recorded in `fixture_pack.json`.

---

## Selection Rules

All selection uses `ManagedArtifactIndex` metadata only — no directory scanning.

Post-selection filters applied in order:

1. **Finding filter** — if `finding_ids` + `findings` provided, restrict to artifacts referenced by those findings
2. **Kind filter** — if `artifact_kind` set, restrict to that kind
3. **Singleton filter** — repo singletons excluded by default; `include_repo_singletons=True` to include
4. **Max artifacts** — truncate to `max_artifacts` if set

`excluded_paths` from the manifest are never harvested as artifacts.

---

## Copy Policy

`CopyPolicy` controls what gets physically copied:

| Field | Default | Notes |
|-------|---------|-------|
| `max_artifact_bytes` | 10 MiB | Per-artifact size cap |
| `max_total_bytes` | 100 MiB | Total pack size cap |
| `allowed_content_types` | `None` | None = allow all text/JSON |
| `include_binary_artifacts` | `False` | Binary content types skipped by default |
| `include_missing_files` | `True` | Missing files recorded as metadata-only |

Artifacts that exceed limits or fail copy constraints are recorded as
`FixtureArtifact(copied=False, copy_error=<reason>)`.

---

## Provenance Requirements

Every fixture pack retains:

- `source_manifest_path` — absolute path to the source `artifact_manifest.json`
- `source_index_summary` — full index stats at harvest time
- `source_manifest.json` — physical copy of the manifest (for offline inspection)
- `source_index_summary.json` — physical copy of the summary JSON
- Per-artifact: `source_artifact_id`, `source_path`, `source_stage`, `location`

A fixture pack can always be traced back to its source run without accessing the
managed repo.

---

## Calibration Finding Integration

To use calibration findings as selection evidence:

```python
from operations_center.behavior_calibration import analyze_artifacts, BehaviorCalibrationInput, AnalysisProfile
from operations_center.fixture_harvesting import harvest_fixtures, HarvestRequest, HarvestProfile

inp = BehaviorCalibrationInput(
    repo_id=index.source.repo_id,
    run_id=index.source.run_id,
    audit_type=index.source.audit_type,
    artifact_index=index,
    analysis_profile=AnalysisProfile.FAILURE_DIAGNOSIS,
)
report = analyze_artifacts(inp)

request = HarvestRequest(
    index=index,
    harvest_profile=HarvestProfile.MINIMAL_FAILURE,
    findings=report.findings,
    finding_ids=[f.finding_id for f in report.findings if f.severity.value == "error"],
)
pack, pack_dir = harvest_fixtures(request, Path("tools/audit/fixtures"))
```

Finding references are stored in `pack.findings` as `FixtureFindingReference` — provenance only, not executable policy.

---

## Fixture Pack Layout

```
tools/audit/fixtures/
  {repo_id}__{run_id}__{profile}__{ts}/
    fixture_pack.json           — pack metadata and artifact entries
    source_manifest.json        — copy of source artifact_manifest.json
    source_index_summary.json   — ArtifactIndexSummary JSON
    artifacts/
      {safe_artifact_filename}  — copied artifact files
```

File names in `artifacts/` are derived from `artifact_id` with unsafe characters
replaced by underscores.

---

## CLI / Tool Entry Points

```bash
# Harvest with default profile (minimal_failure)
operations-center-fixtures harvest \
    --manifest path/to/artifact_manifest.json \
    --profile minimal_failure

# Harvest a specific stage slice
operations-center-fixtures harvest \
    --manifest path/to/artifact_manifest.json \
    --profile stage_slice \
    --stage TopicSelectionStage \
    --output-dir tools/audit/fixtures

# Harvest explicit artifact IDs
operations-center-fixtures harvest \
    --manifest path/to/artifact_manifest.json \
    --profile manual_selection \
    --artifact-id "managed-private-project:representative:TopicSelectionStage:topic_selection"

# Inspect a fixture pack
operations-center-fixtures inspect \
    --fixture-pack tools/audit/fixtures/{pack_id}/

# List all fixture packs
operations-center-fixtures list \
    --root tools/audit/fixtures
```

---

## Non-Goals

Phase 9 explicitly does **not**:

- Execute replay tests (Phase 10)
- Create regression suites
- Mutate original producer artifacts
- Mutate manifests
- Apply calibration recommendations
- Import managed repo code
- Scan directories to discover artifacts
- Harvest `excluded_paths` as artifacts
- Produce `CalibrationDecision` objects
- Modify managed repo configuration

---

## Module Layout

```
src/operations_center/fixture_harvesting/
    __init__.py     — public exports
    errors.py       — FixtureHarvestingError hierarchy
    models.py       — FixturePack, FixtureArtifact, FixtureFindingReference, HarvestProfile, HarvestRequest, CopyPolicy
    selector.py     — select_fixture_artifacts()
    harvester.py    — harvest_fixtures() (main entry point)
    writer.py       — write_fixture_pack()
    loader.py       — load_fixture_pack()

src/operations_center/entrypoints/fixtures/
    main.py         — operations-center-fixtures CLI (Typer)
```
