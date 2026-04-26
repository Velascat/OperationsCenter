# VideoFoundry Audit Artifact Contract

**Contract name:** `managed-repo-audit`  
**Schema version:** `1.0`  
**Producer:** `videofoundry`  
**Related:** [Managed Repo Contract](videofoundry_managed_repo_contract.md) · [Ground Truth](videofoundry_audit_ground_truth.md)

---

## Purpose

This document defines the artifact contract between OperationsCenter and VideoFoundry for audit runs. OperationsCenter invokes VideoFoundry audit commands and reads the outputs; it never imports VideoFoundry Python code. The contract specifies exactly what files VF must produce, in what shape, so OpsCenter can ingest them without knowing VF internals.

---

## Boundary

```
OperationsCenter                VideoFoundry
─────────────────────────       ─────────────────────────
Defines contract schemas        Implements contract schemas
Generates run_id (uuid4.hex)    Receives run_id via $AUDIT_RUN_ID
Invokes VF commands             Runs audit, writes contract files
Reads run_status.json           Writes run_status.json
Reads artifact_manifest.json    Writes artifact_manifest.json
```

OpsCenter may only invoke commands and read files. No Python imports across the boundary.

---

## Phase 0 Ground Truth Inputs

The contract reflects findings from Phase 0 discovery (`videofoundry_audit_ground_truth.md`):

- **Only one audit type (representative) has run_status finalization.** The five others (`enrichment`, `ideation`, `render`, `segmentation`, `stack_authoring`) write an initial `in_progress` status via `prepare_audit_bucket()` but never finalize it. Phase 5 must add finalization to all six.
- **No `artifact_manifest.json` exists yet.** The field `artifact_manifest_path` is absent from all current VF run_status files. The contract makes it `Optional[str]` to accept legacy files, but `is_compliant` returns `False` unless it is present.
- **Legacy status value:** VF currently emits `"in_progress"`. The contract canonicalizes running state as `"running"`. The enum value `IN_PROGRESS_LEGACY = "in_progress"` is accepted and marked non-compliant.
- **stack_authoring output dir:** `tools/audit/report/authoring`, not `tools/audit/report/stack_authoring`. Phase 0 discovered this quirk.
- **Architecture invariants** are written to a fixed repo path, not per-run buckets.

---

## Phase 1 Managed Repo Relationship

The managed-repo config (`config/managed_repos/videofoundry.yaml`, loaded by `managed_repos.loader`) tells OpsCenter how to invoke VF commands. This artifact contract defines what those commands produce. The two are complementary:

- Phase 1 config → how to invoke, where to look for outputs
- Phase 2 contract (this document) → what the output files must contain

---

## Contract Files

| File | Path | Module |
|------|------|--------|
| Controlled vocabulary | `src/operations_center/audit_contracts/vocabulary.py` | `audit_contracts.vocabulary` |
| Run status model | `src/operations_center/audit_contracts/run_status.py` | `audit_contracts.run_status` |
| Artifact manifest model | `src/operations_center/audit_contracts/artifact_manifest.py` | `audit_contracts.artifact_manifest` |
| VF producer profile | `src/operations_center/audit_contracts/profiles/videofoundry.py` | `audit_contracts.profiles` |
| JSON schemas | `schemas/audit_contracts/` | generated from Pydantic |
| Examples | `examples/audit_contracts/` | validated against models |

---

## Controlled Vocabulary

### Two-Layer Design

The vocabulary is split into two explicit layers:

**Generic managed-repo enums** (reusable by any producer):

| Enum | Key values |
|------|-----------|
| `RunStatus` | `pending`, `running`, `completed`, `failed`, `interrupted`, `unknown`, `in_progress` (legacy) |
| `ManifestStatus` | `initializing`, `running`, `completed`, `failed`, `partial`, `unknown` |
| `Location` | `run_root`, `artifacts_subdir`, `audit_subdir`, `text_overlay_subdir`, `repo_singleton`, `external_or_unknown` |
| `PathRole` | `primary_output`, `intermediate`, `audit_artifact`, `config`, `log`, `unknown` |
| `ArtifactStatus` | `present`, `missing`, `stale`, `unknown` |
| `ConsumerType` | `human_review`, `automated_analysis`, `fixture_harvesting`, `slice_replay`, `regression_testing`, `architecture_invariant_verification`, `failure_diagnosis`, `unknown` |
| `ValidFor` | `current_run_only`, `cross_run_comparison`, `latest_snapshot`, `historical_record`, `partial_run_analysis`, `unknown` |
| `Limitation` | `partial_run`, `missing_downstream_artifacts`, `producer_not_finalized`, `non_representative_audit_unverified`, `repo_singleton_overwritten`, `infrastructure_noise_excluded`, `path_layout_non_uniform`, `unknown` |

**VideoFoundry profile enums** (VF-specific, in `VIDEOFOUNDRY_PROFILE_ENUMS`):

| Enum | Description |
|------|-------------|
| `VideoFoundryAuditType` | Six audit types: `representative`, `enrichment`, `ideation`, `render`, `segmentation`, `stack_authoring` |
| `VideoFoundrySourceStage` | Known stage names from Phase 0 (TopicSelectionStage, etc.) |
| `VideoFoundryArtifactKind` | Artifact kinds: `run_status`, `stage_report`, `audit_report`, `architecture_invariant`, etc. |

`GENERIC_ENUMS` and `VIDEOFOUNDRY_PROFILE_ENUMS` are disjoint tuples enforced by tests.

---

## run_status.json — Entry Point

OpsCenter discovers audit state by reading `run_status.json` at the path defined in the managed-repo config. This file is the entry point for the output discovery chain.

### Schema (`ManagedRunStatus`)

Required fields:

| Field | Type | Notes |
|-------|------|-------|
| `producer` | `str` | e.g. `"videofoundry"` |
| `repo_id` | `str` | e.g. `"videofoundry"` |
| `run_id` | `str` | uuid4().hex, injected by OpsCenter via `$AUDIT_RUN_ID` |
| `audit_type` | `str` | one of the six VF audit types |
| `status` | `RunStatus` | current run state |

Optional but contract-required for compliance:

| Field | Type | Notes |
|-------|------|-------|
| `artifact_manifest_path` | `str \| None` | path to manifest; `is_compliant` is False when absent |
| `current_phase` | `str \| None` | phase label set by `_RunStatusFinalizer` |
| `started_at` / `updated_at` / `completed_at` | `datetime \| None` | timing |
| `error` / `traceback` | `str \| None` | failure details |
| `metadata` | `dict` | channel_slug, etc. |

Defaults: `schema_version = "1.0"`, `contract_name = "managed-repo-audit"`.

### Computed Properties

| Property | Meaning |
|----------|---------|
| `is_terminal` | True for `completed`, `failed`, `interrupted` |
| `has_manifest` | True iff `artifact_manifest_path` is not None |
| `is_compliant` | True iff `has_manifest` and status is not `IN_PROGRESS_LEGACY` |

### Legacy Handling

Files with `status: "in_progress"` parse as `RunStatus.IN_PROGRESS_LEGACY`. `is_compliant` returns `False`. This marks the file as pre-Phase-5 and non-compliant without rejecting it.

---

## artifact_manifest.json — Artifact Inventory

Linked from `run_status.json` via `artifact_manifest_path`. Contains the full inventory of artifacts produced by the run.

### Schema (`ManagedArtifactManifest`)

| Field | Type | Notes |
|-------|------|-------|
| `producer`, `repo_id`, `run_id`, `audit_type` | `str` | mirrors run_status |
| `manifest_status` | `ManifestStatus` | lifecycle state of the manifest itself |
| `run_status` | `RunStatus` | final run state at manifest creation |
| `created_at` / `updated_at` | `datetime` | manifest timestamps |
| `artifacts` | `list[ManagedArtifactEntry]` | all tracked artifacts |
| `excluded_paths` | `list[ExcludedPath]` | infrastructure noise, not artifacts |
| `warnings` / `errors` | `list[str]` | run-level diagnostics |
| `limitations` | `list[Limitation]` | e.g. `partial_run`, `missing_downstream_artifacts` |

Computed:

| Property | Meaning |
|----------|---------|
| `is_terminal` | manifest_status in {completed, failed, partial} |
| `singleton_artifacts` | artifacts with `location == repo_singleton` |
| `run_scoped_artifacts` | artifacts with run-scoped locations |
| `artifact_by_id(id)` | lookup by artifact_id |

### ManagedArtifactEntry

Each artifact entry:

| Field | Type | Notes |
|-------|------|-------|
| `artifact_id` | `str` | `producer:audit_type:stage:kind` format |
| `artifact_kind` | `str` | from `VideoFoundryArtifactKind` |
| `path` | `str` | path relative to repo root |
| `location` | `Location` | which bucket/subdir the artifact lives in |
| `status` | `ArtifactStatus` | `present` or `missing` |
| `consumer_types` | `list[ConsumerType]` | intended consumers |
| `valid_for` | `list[ValidFor]` | snapshot/run/historical scoping |
| `limitations` | `list[Limitation]` | artifact-level limitations |

---

## Artifact Location Types

Five location types must appear in a completed representative manifest:

| Location | Physical path pattern |
|----------|-----------------------|
| `run_root` | `tools/audit/report/representative/{bucket}/` |
| `artifacts_subdir` | `tools/audit/report/representative/{bucket}/artifacts/` |
| `audit_subdir` | `tools/audit/report/representative/{bucket}/audit/` |
| `text_overlay_subdir` | `tools/audit/report/representative/{bucket}/text_overlay/` |
| `repo_singleton` | `tools/audit/report/architecture_invariants/latest.json` |

The `repo_singleton` location is distinct from run locations — it is not scoped to a bucket.

---

## Repo Singleton Rules

The architecture invariants file is written to a fixed path after every representative run, overwriting the previous version. Contract rules:

- `location = repo_singleton`
- `valid_for` must include `latest_snapshot`
- `limitations` must include `repo_singleton_overwritten`
- `relative_path` is `None` (not relative to a run bucket)
- The `singleton_artifacts` property on the manifest returns all such entries.

---

## Incremental Manifest Writing

A manifest is valid at any lifecycle stage, not only after finalization. OpsCenter must handle all states:

| `manifest_status` | Meaning | `is_terminal` |
|-------------------|---------|---------------|
| `initializing` | manifest created, no artifacts yet | False |
| `running` | artifacts being added during run | False |
| `completed` | all artifacts present | True |
| `partial` | run interrupted, some artifacts present | True |
| `failed` | run failed before producing artifacts | True |

A `partial` manifest with `run_status = interrupted` is the expected shape for interrupted runs.

---

## Excluded Paths Rules

Infrastructure files that appear on disk alongside run artifacts must NOT be listed in `artifacts`. They go in `excluded_paths`:

| Path / Pattern | Reason |
|----------------|--------|
| `coverage.ini` | pytest coverage config, infrastructure noise |
| `.coverage*` | coverage data files |
| `sitecustomize.py` | injected by dev tooling |
| `__pycache__/**` | Python bytecode |
| `*.pyc` | Python bytecode |

`ExcludedPath` model: `path` (required), `reason` (required), `pattern` (optional, the glob that matched).

Tests enforce that no path appearing in `excluded_paths` also appears in `artifacts`.

---

## Completed Run Example

`examples/audit_contracts/completed_run_status.json` — `status: completed`, `artifact_manifest_path` populated.

`examples/audit_contracts/completed_artifact_manifest.json` — 10 artifacts covering all 5 location types, 3 excluded paths, `manifest_status: completed`.

Both parse cleanly against Pydantic models and are validated by `tests/unit/audit_contracts/test_examples.py`.

---

## Failed/Partial Run Example

`examples/audit_contracts/failed_run_status.json` — `status: interrupted`, error message present.

`examples/audit_contracts/failed_artifact_manifest.json` — `manifest_status: partial`, `run_status: interrupted`, at least one `ArtifactStatus.MISSING` artifact, limitations include `partial_run` and `missing_downstream_artifacts`.

---

## Phase 5 Implementation Requirements

VideoFoundry must implement these changes for `is_compliant` to return `True`:

1. **Write `artifact_manifest.json`** after every audit run (all six types).
2. **Set `artifact_manifest_path`** in `run_status.json` pointing to the manifest.
3. **Use `"running"` instead of `"in_progress"`** as the in-progress status value.
4. **Add `run_status_finalization`** to the five audit types that currently only call `prepare_audit_bucket()`. Each must write a terminal status (`completed` or `interrupted`) at exit.
5. **Populate `excluded_paths`** in each manifest with the known infrastructure noise patterns.
6. **Include the repo singleton** in the manifest for representative runs.

Until Phase 5, OpsCenter treats all VF run_status files as legacy (`is_compliant = False`) and reads them in read-only diagnostic mode.

---

## Cross-Repo Reuse Pattern

The generic contract layer (`vocabulary.py`, `run_status.py`, `artifact_manifest.py`) has no VideoFoundry-specific code. A second managed repo defines its own profile:

```python
from operations_center.audit_contracts.profiles import VideoFoundryProducerProfile

OTHER_PROFILE = VideoFoundryProducerProfile(
    producer_id="other_repo",
    audit_type_specs=[...],
    known_source_stages=[...],
    known_artifact_kinds=[...],
)
```

The generic contract models (ManagedRunStatus, ManagedArtifactManifest) are unchanged. The boundary enforcement test (`TestBoundaryEnforcement`) uses Python AST to verify the audit_contracts package never imports VF code.

---

## Generic Contract vs VideoFoundry Profile

| Layer | What it contains | Who can use it |
|-------|-----------------|----------------|
| Generic contract | RunStatus, ManifestStatus, Location, ManagedRunStatus, ManagedArtifactManifest | Any managed repo |
| VF producer profile | VideoFoundryAuditType, VideoFoundryAuditTypeSpec, VIDEOFOUNDRY_PROFILE | VideoFoundry only |

`GENERIC_ENUMS` and `VIDEOFOUNDRY_PROFILE_ENUMS` tuples are exported from `vocabulary.py` to allow tests to assert the layers are disjoint.

---

## Non-Goals

- OpsCenter does not validate artifact file contents — only the manifest metadata.
- OpsCenter does not write `run_status.json` or `artifact_manifest.json` — VF writes them.
- The contract does not specify how VF internally structures its stages or pipeline.
- OpsCenter does not import any VideoFoundry Python code at any point.
