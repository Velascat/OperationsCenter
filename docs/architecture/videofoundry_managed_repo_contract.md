# VideoFoundry Managed Repo Contract

**Phase**: 1 — Managed Repo Contract
**Config**: `config/managed_repos/videofoundry.yaml`
**Models**: `src/operations_center/managed_repos/`
**Tests**: `tests/unit/managed_repos/test_videofoundry_config.py`
**Ground truth**: `docs/architecture/videofoundry_audit_ground_truth.md`

---

## Purpose

VideoFoundry runs multi-phase audit pipelines that produce structured artifact outputs.
OperationsCenter needs to invoke those audits, inject run identity, and later read the outputs.

This contract makes VideoFoundry visible to OpsCenter as a managed external repository
without coupling OpsCenter to VideoFoundry's Python internals.

---

## Boundary

```
OperationsCenter = contract owner + orchestrator
VideoFoundry     = audit runner + artifact producer
```

**OpsCenter may:**
- invoke VideoFoundry commands via subprocess
- read files produced by VideoFoundry (`run_status.json`, `artifact_manifest.json`)
- set environment variables for VideoFoundry processes (`AUDIT_RUN_ID`)

**OpsCenter must never:**
- import any VideoFoundry Python module
- depend on VideoFoundry Python package internals
- scan arbitrary directories to discover artifacts — use the `run_status.json → artifact_manifest_path` discovery chain only (once Phase 2+5 are complete)
- call VideoFoundry functions directly
- read VideoFoundry source code at runtime

---

## Config Location

```
config/managed_repos/videofoundry.yaml
```

This is a standalone YAML file, separate from `config/operations_center.yaml`.

Reason: VideoFoundry is not a kodo execution target (no PRs, no validation pipeline).
It is an external service that OpsCenter invokes and reads from. The managed repo
contract is a different concept from the existing `repos:` block.

---

## Audit Capability

VideoFoundry exposes one capability: `audit`.

The audit capability lets OpsCenter:
1. generate a `run_id` (UUID hex)
2. set `AUDIT_RUN_ID` in the audit process environment
3. invoke an audit command
4. poll `run_status.json` for lifecycle state
5. (future, Phase 2+5) follow `artifact_manifest_path` → `artifact_manifest.json` for artifact discovery

---

## Supported Audit Types

| Audit Type | Command | Output Dir | Verified |
|---|---|---|---|
| `representative` | `python -m tools.audit.run_representative_audit` | `tools/audit/report/representative/` | Yes — one real run inspected |
| `enrichment` | `python -m tools.audit.run_enrichment_audit` | `tools/audit/report/enrichment/` | Source only — no real run |
| `ideation` | `python -m tools.audit.run_ideation_audit` | `tools/audit/report/ideation/` | Source only — no real run |
| `render` | `python -m tools.audit.run_render_audit` | `tools/audit/report/render/` | Source only — no real run |
| `segmentation` | `python -m tools.audit.run_segmentation_audit` | `tools/audit/report/segmentation/` | Source only — no real run |
| `stack_authoring` | `python -m tools.audit.run_stack_authoring_audit` | `tools/audit/report/authoring/` | Source only — no real run |

All commands run from the VideoFoundry repo root (`../VideoFoundry` relative to OpsCenter).

**Naming note**: the `stack_authoring` audit type writes to `tools/audit/report/authoring/` — the directory name is `authoring`, not `stack_authoring`.

---

## Run ID Injection

OpsCenter is the source of truth for `run_id`.

```
1. OpsCenter generates: run_id = uuid4().hex  (32 hex chars, no dashes)
2. OpsCenter sets:      AUDIT_RUN_ID={run_id} in the audit subprocess env
3. VideoFoundry reads:  AUDIT_RUN_ID at startup and uses it as the run identity
4. VideoFoundry writes: run_id into run_status.json
5. OpsCenter reads:     run_id back from run_status.json to confirm identity
```

VideoFoundry has a local/dev fallback (generates its own run_id if `AUDIT_RUN_ID` is absent),
but OpsCenter must not depend on that fallback. Managed runs always inject `AUDIT_RUN_ID`.

---

## Output Discovery

### Planned chain (Phase 2 + Phase 5 required)

```
{bucket_dir}/run_status.json
  → field: artifact_manifest_path
    → {artifact_manifest_path}  ← artifact_manifest.json
```

### Current reality (Phase 0 ground truth)

`artifact_manifest_path` does **not** exist in `run_status.json` today.

`artifact_manifest.json` does **not** exist anywhere in VideoFoundry today.

Do not attempt manifest discovery until Phase 2 defines the schema and Phase 5
adds the implementation to VideoFoundry.

### run_status.json — current shape

```json
{
  "status": "in_progress",
  "run_id": "3dead998d4c44e1cb296bef061de50f3",
  "timestamp": "20260426_153453",
  "current_phase": "rendering"
}
```

On success (representative only):
```json
{ "status": "complete", "run_id": "...", "current_phase": "post_run_audit", "exit_code": 0 }
```

On failure (representative only):
```json
{ "status": "failed", "run_id": "...", "current_phase": "...", "error": "...", "traceback": "..." }
```

### Bucket naming

```
{channel_slug}_{YYYYMMDD}_{HHMMSS}_{run_id_hex}
```

Example: `Connective_Contours_20260426_153453_3dead998d4c44e1cb296bef061de50f3`

---

## Known Phase 0 Gaps

These gaps are documented as data in the config, not hidden.

### run_status.json finalization (critical)

Only the `representative` audit has lifecycle finalization (`_RunStatusFinalizer`).

The other 5 audit types write `in_progress` at start via `prepare_audit_bucket()` and
**never write a final status**. `run_status.json` remains stuck at `in_progress` after
completion or failure for `enrichment`, `ideation`, `render`, `segmentation`, and `stack_authoring`.

OpsCenter cannot distinguish a running, completed, or failed non-representative audit
from `run_status.json` alone — until Phase 5 adds finalization to those types.

### No completed runs for 5 of 6 audit types

Only one real run exists: `representative`, interrupted during rendering. No evidence
of what `enrichment`, `ideation`, `render`, `segmentation`, or `stack_authoring` produce
from a complete run.

### No artifact manifest

`artifact_manifest.json` does not exist. `artifact_manifest_path` is not in `run_status.json`.
Both require Phase 2 (schema contract) + Phase 5 (VideoFoundry implementation).

---

## Non-Goals

Phase 1 does **not**:

- define the artifact manifest schema (Phase 2)
- add `artifact_manifest_path` to VideoFoundry's `run_status.json` (Phase 5)
- fix the run_status.json finalization gap for non-representative audits (Phase 5)
- implement artifact reading or indexing (Phase 7)
- implement dispatch orchestration (Phase 6)
- define controlled vocabulary enums (Phase 2)
- create fixture packs or slice replay tests (Phases 9–10)
- change any VideoFoundry behavior
