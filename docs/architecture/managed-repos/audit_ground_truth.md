# the managed repo Audit Ground Truth Report

**Phase**: 0 — Ground Truth (Read-Only Discovery)
**Date**: 2026-04-26
**Status**: Complete

---

## Scope

Read-only inspection of real the managed repo audit runs and source code to understand what the system actually produces today — before OpsCenter defines contracts around those outputs.

No schemas were created. No implementation was changed. No contracts were designed.

---

## Repositories Inspected

- `the managed repo` — audit runner, artifact producer
- `OperationsCenter` — contract owner (no VF code imported; only files read)

---

## Audit Types Covered

| Audit Type | CLI Script | Default Report Dir | Has Real Run |
|---|---|---|---|
| `representative` | `tools/audit/cli/run_representative_audit.py` | `tools/audit/report/representative/` | Yes — in_progress |
| `enrichment` | `tools/audit/cli/run_enrichment_audit.py` | `tools/audit/report/enrichment/` | No runs |
| `ideation` | `tools/audit/cli/run_ideation_audit.py` | `tools/audit/report/ideation/` | No runs |
| `render` | `tools/audit/cli/run_render_audit.py` | `tools/audit/report/render/` | No runs |
| `segmentation` | `tools/audit/cli/run_segmentation_audit.py` | `tools/audit/report/segmentation/` | No runs |
| `stack_authoring` | `tools/audit/cli/run_stack_authoring_audit.py` | `tools/audit/report/authoring/` | No runs |

Note: the directory for `stack_authoring` is named `authoring`, not `stack_authoring`. The command name and the directory name do not match.

---

## Runs Inspected

### Run 1 — representative (in_progress, interrupted)

```
bucket_dir:  tools/audit/report/representative/Connective_Contours_20260426_153453_3dead998d4c44e1cb296bef061de50f3/
run_id:      3dead998d4c44e1cb296bef061de50f3
timestamp:   20260426_153453
audit_type:  representative
status:      in_progress
current_phase: rendering
channel_slug: Connective_Contours
```

This run reached the rendering phase and was interrupted. Authoring-phase artifacts are present and complete. Rendering-phase artifacts (text_overlay/, voice_over, delivery) are absent or empty.

---

## run_status.json Findings

### Shape of current run_status.json

```json
{
  "status": "in_progress",
  "run_id": "3dead998d4c44e1cb296bef061de50f3",
  "timestamp": "20260426_153453",
  "current_phase": "rendering"
}
```

### Fields observed

| Field | Present | Type | Notes |
|---|---|---|---|
| `status` | Always | string | Values: `in_progress`, (never finalized in this run) |
| `run_id` | Always | string | UUID hex from `uuid4().hex` |
| `timestamp` | Always | string | `YYYYMMDD_HHMMSS` format, UTC |
| `current_phase` | Yes | string | Updated incrementally as phases progress |
| `error` | Not present | string | Written only on failure finalization |
| `traceback` | Not present | string | Written only on failure finalization |
| `exit_code` | Not present | int | Written only on successful finalization |
| `abnormal_exit` | Not present | bool | Written on signal/atexit failure |
| `artifact_manifest_path` | **Not present** | — | Does not exist yet — Phase 2 deliverable |

### How run_status.json is written

`write_run_status()` in `tools/audit/api/report_bucket.py`:
- Reads existing file if present, merges payload on top, rewrites atomically
- This means fields accumulate over the lifecycle (not clobbered)

### Phase progression (representative only)

```
bootstrap → authoring → rendering → pre_delivery_voice_over_gate → pre_delivery_text_render_gate → delivery → post_run_audit → [complete | failed]
```

### Critical gap: 5 of 6 audit types never finalize run_status.json

- `representative`: has `_RunStatusFinalizer` — writes phase updates and final status (complete/failed/signal/atexit)
- `enrichment`, `ideation`, `render`, `segmentation`, `stack_authoring`: call `prepare_audit_bucket()` which writes initial `in_progress`, then **never write a final status**

If a non-representative audit completes normally or fails, `run_status.json` remains stuck at `in_progress`. OpsCenter cannot distinguish a running, completed, or failed non-representative audit from `run_status.json` alone.

### Paths in run_status.json

- All fields are strings or scalars — no file paths are present in current `run_status.json`
- `artifact_manifest_path` field does not exist

### Incremental writing

- `representative`: yes — `set_phase()` called at each major pipeline transition
- Other audit types: no — single write at start only

---

## Artifact Inventory

### Representative Audit — Confirmed Present (from in_progress run)

#### Location: run_root (top-level in bucket)

| File | Extension | Size | Producer Stage | Machine-Readable | Notes |
|---|---|---|---|---|---|
| `run_status.json` | .json | 143B | lifecycle | Yes | Entry-point for OpsCenter |
| `Connective_Contours_Connective_Contours_topic_selection.json` | .json | 4.8K | TopicSelectionStage | Yes | Selected topic + recent/suggested/random topics |
| `Connective_Contours_Connective_Contours_outline__outline_planning.json` | .json | 14K | OutlinePlanningStage | Yes | Topic + outline with talking points |
| `Connective_Contours_Connective_Contours_sources__script_writing.json` | .json | 6.4K | ScriptWritingStage | Yes | Sources used in script |
| `Connective_Contours_final_script__script_writing.txt` | .txt | 292B | ScriptWritingStage | Human | Final script text (truncated/partial in interrupted run) |
| `Connective_Contours_Connective_Contours_fact_check_draft.json` | .json | 6.1K | FactCheckStage | Yes | Draft fact-check findings |
| `Connective_Contours_Connective_Contours_fact_check.json` | .json | 1.2K | StructuredFactCheckStage | Yes | Per-scene fact check scores + issues |
| `Connective_Contours_Connective_Contours_provisional_script__fact_check_draft.txt` | .txt | 292B | FactCheckStage | Human | Provisional script text |
| `Connective_Contours_Connective_Contours_provisional_script__fact_check.txt` | .txt | 3.2K | StructuredFactCheckStage | Human | Structured fact check output text |
| `Connective_Contours_final_script__fact_check_draft.txt` | .txt | 292B | FactCheckStage | Human | Final script after fact-check draft |
| `Connective_Contours_final_script__fact_check_structured.txt` | .txt | 3.2K | StructuredFactCheckStage | Human | Structured fact-check of final script |
| `Connective_Contours_Connective_Contours_script_segmentation.json` | .json | 12K | ScriptSegmentationStage | Yes | Per-scene segmentation |
| `Connective_Contours_script_object__script_segmentation.json` | .json | 1.5M | ScriptSegmentationStage | Yes | Full script object post-segmentation (large) |
| `Connective_Contours_Connective_Contours_script_enrichment_critique_summary.json` | .json | 1.2K | ScriptEnrichmentStage | Yes | Enrichment critique summary (accepted/rejected/violation counts) |
| `Connective_Contours_script_object__script_enrichment.json` | .json | 1.6M | ScriptEnrichmentStage | Yes | Full script object post-enrichment (large) |
| `Connective_Contours_AuthorProsodyStage.json` | .json | 53K | AuthorProsodyStage | Yes | Per-line prosody flags, chunk ids, pause timings |
| `Connective_Contours_AuthorProsodyStage.txt` | .txt | 6.8K | AuthorProsodyStage | Human | Prose prosody summary |
| `Connective_Contours_voice_over_asr_observations.jsonl` | .jsonl | 130K | VoiceOverStage | Yes | Per-segment ASR observations (path, expected_text, asr_text, similarity, timing) |
| `coverage.ini` | .ini | 310B | infrastructure | No | Coverage tool config — not an audit artifact |
| `sitecustomize.py` | .py | 107B | infrastructure | No | Coverage bootstrap — not an audit artifact |
| `.coverage.*` | binary | — | infrastructure | No | Coverage data files — not audit artifacts |

#### Location: artifacts/authoring/

| File | Extension | Size | Producer Stage | Notes |
|---|---|---|---|---|
| `Connective_Contours_evidence_queries.json` | .json | 297B | authoring | Search queries used for evidence gathering |
| `Connective_Contours_evidence_pack.json` | .json | 11K | authoring | Evidence pack with retrieved content |

#### Location: artifacts/script_contract/ScriptWriting/

| File | Extension | Size | Producer Stage | Notes |
|---|---|---|---|---|
| `Connective_Contours_script_draft.json` | .json | 3.9K | ScriptWritingStage | Script draft as JSON (script_id + scenes array) |
| `Connective_Contours_script_draft.md` | .md | 3.6K | ScriptWritingStage | Script draft as markdown |
| `Connective_Contours_script_draft.delimited.txt` | .txt | 3.7K | ScriptWritingStage | Script draft as delimited text |

#### Location: audit/

| File | Extension | Size | Producer Stage | Notes |
|---|---|---|---|---|
| `Connective_Contours_audit__voicesamplecleanstage__deadcode_report.txt` | .txt | 45K | VoiceSampleCleanStage deadcode runner | Dead code report for voice sample subsystem |

#### Location: text_overlay/ (directory exists, empty in interrupted run)

Expected but absent:
- `*timeline_scene_*.json` — per-scene text overlay timeline plan
- `*text_overlay_scene_*.json` — per-scene text overlay snapshot (from renderer)

These are produced only after rendering completes.

#### Written by run_feature_audit (post-run, via FileArtifactStore into report_dir)

These are expected at run_root but absent because the run was interrupted before `post_run_audit` phase:

| File | Kind | Notes |
|---|---|---|
| `voice_over_coverage_audit.jsonl` | voice_over_coverage_audit | Per-segment VO coverage detail |
| `scene_duration_reconcile_audit.jsonl` | — | Scene duration reconciliation detail |
| `voice_over_coverage_summary.json` | voice_over_coverage_summary | Aggregate VO coverage gate results |
| `text_overlay_sync_summary.json` | — | Text overlay sync aggregate |
| `text_render_quality_audit.jsonl` | text_render_quality_audit | Per-scene text render quality detail |
| `text_render_quality_summary.json` | text_render_quality_summary | Aggregate text render quality gate results |
| `voice_over_regression_gate.json` | voice_over_regression_gate | Gate pass/fail verdict |
| `text_render_regression_gate.json` | text_render_regression_gate | Gate pass/fail verdict |
| `final_delivery_quality_summary.json` | final_delivery_quality_summary | Overall delivery quality summary |
| `final_delivery_quality_summary.md` | — | Human-readable delivery quality summary |

#### Repository singleton: architecture_invariants

```
tools/audit/report/architecture_invariants/latest.json
tools/audit/report/architecture_invariants/latest.md
tools/audit/report/architecture_invariants/baseline.json
tools/audit/report/architecture_invariants/warning_triage.json
tools/audit/report/architecture_invariants/warning_triage.md
```

`latest.json` shape:
```json
{
  "status": "warn",
  "checked_at": "2026-04-25T23:03:05.350975+00:00",
  "repo_root": "/home/dev/Documents/GitHub/the managed repo",
  "summary": { "pass": 41, "warn": 190, "fail": 0, "known_legacy": 7 },
  "findings": [
    {
      "id": "VF-ARCH-LAYER-001",
      "family": "layer_direction",
      "severity": "warn",
      "status": "known_legacy",
      "path": "src/module/text_render/service.py",
      "line": 13,
      "message": "module/ must not import api/",
      "evidence": "import: api.text_render.client",
      "suggested_fix": "..."
    }
  ]
}
```

This is not inside a per-run bucket — it is a repo-level singleton written at a fixed path independent of any run. It has no `run_id`. It is overwritten in-place on each architecture invariant run.

---

## Path Layout Patterns

Four distinct path patterns are observed:

### Pattern 1: run_root
Files written directly into the bucket directory root.

```
tools/audit/report/representative/{bucket}/
├── run_status.json
├── {prefix}_topic_selection.json
├── {prefix}_outline__outline_planning.json
├── {prefix}_fact_check.json
├── {prefix}_script_segmentation.json
├── {prefix}_script_object__script_segmentation.json
├── {prefix}_script_object__script_enrichment.json
├── {prefix}_script_enrichment_critique_summary.json
├── {prefix}_AuthorProsodyStage.json
├── {prefix}_AuthorProsodyStage.txt
├── {prefix}_voice_over_asr_observations.jsonl
├── {prefix}_final_script__*.txt
├── {prefix}_provisional_script__*.txt
├── voice_over_coverage_audit.jsonl          ← post-run
├── voice_over_coverage_summary.json          ← post-run
├── text_render_quality_audit.jsonl           ← post-run
├── text_render_quality_summary.json          ← post-run
├── voice_over_regression_gate.json           ← post-run
├── text_render_regression_gate.json          ← post-run
├── final_delivery_quality_summary.json       ← post-run
└── final_delivery_quality_summary.md         ← post-run
```

### Pattern 2: artifacts/ subdirectory
Files written into named subdirectories under `artifacts/`.

```
{bucket}/artifacts/
├── authoring/
│   ├── {prefix}_evidence_queries.json
│   └── {prefix}_evidence_pack.json
└── script_contract/
    └── ScriptWriting/
        ├── {prefix}_script_draft.json
        ├── {prefix}_script_draft.md
        └── {prefix}_script_draft.delimited.txt
```

### Pattern 3: named subdirectory (audit/, text_overlay/)
```
{bucket}/audit/
└── {prefix}_audit__{stage}__deadcode_report.txt

{bucket}/text_overlay/
├── *timeline_scene_*.json      (expected, not present in interrupted run)
└── *text_overlay_scene_*.json  (expected, not present in interrupted run)
```

### Pattern 4: repo-level singleton (architecture_invariants)
Not inside any per-run bucket. Written to a fixed path in the report tree.

```
tools/audit/report/architecture_invariants/
├── latest.json           ← overwritten on each run
├── latest.md             ← overwritten on each run
├── baseline.json
├── warning_triage.json
└── warning_triage.md
```

### File naming prefix logic

The `{prefix}` in artifact names is the channel name, sanitized and underscore-separated. For example, `Connective_Contours_` is the prefix for the "Connective Contours" channel. This is resolved by `channel_file_prefix()` in `tools/audit/util/report_naming.py`.

### Bucket directory naming

```
{channel_slug}_{YYYYMMDD}_{HHMMSS}_{run_id_hex}
```

Example: `Connective_Contours_20260426_153453_3dead998d4c44e1cb296bef061de50f3`

`run_id` is `uuid4().hex` (32-char hex, no dashes).

---

## Failure Behavior

### No failed run exists in the repo today

No completed or failed run was available for direct inspection. The only run present was interrupted mid-execution at the `rendering` phase.

### Failure behavior inferred from source code

For the `representative` audit:
- `_RunStatusFinalizer._atexit_finalize()` fires on process exit without explicit finalization
- Writes `status: failed` with `error: "representative audit exited without final status"` and `abnormal_exit: true`
- On SIGINT/SIGTERM: writes `status: failed` with `error: "terminated by signal {NAME}"`, `signal`, `abnormal_exit: true`
- On exception during `execute_representative_workflow`: calls `status_finalizer.finalize(status="failed", details={"error": repr(exc), "traceback": format_exc()})`

**Expected failed run_status.json shape:**
```json
{
  "status": "failed",
  "run_id": "...",
  "timestamp": "...",
  "current_phase": "rendering",
  "error": "...",
  "traceback": "...",
  "abnormal_exit": true
}
```

For the other 5 audit types:
- Failure is not recorded in `run_status.json`
- The file stays at `in_progress` regardless of outcome

### Partial artifact behavior

- `persist_partial_artifacts()` in the representative finalizer attempts to run voice-over and text-render checks before finalizing status on abnormal exit
- Errors in partial artifact persistence are logged as warnings, not re-raised
- Other audit types: no partial artifact persistence logic

### Coverage artifacts in failed/interrupted runs

`coverage.ini` and `.coverage.*` files and `sitecustomize.py` appear in the bucket even in interrupted runs. These are infrastructure artifacts written by the Python coverage tool, not by the audit system.

---

## Observed Vocabulary

### Audit types (as named in code)

```
representative
enrichment
ideation
render
segmentation
stack_authoring   (directory: authoring)
```

### Phases (representative only)

```
bootstrap
authoring
rendering
pre_delivery_voice_over_gate
pre_delivery_text_render_gate
delivery
post_run_audit
```

### Status values (representative only, as written to run_status.json)

```
in_progress
complete
failed
unknown   (inferred by bucket_reader when file is absent/unreadable)
```

### Stage names observed in artifact filenames

```
TopicSelectionStage        → topic_selection
OutlinePlanningStage       → outline__outline_planning
ScriptWritingStage         → script_writing / script_draft
ScriptSegmentationStage    → script_segmentation
ScriptEnrichmentStage      → script_enrichment / script_enrichment_critique_summary
FactCheckStage             → fact_check_draft
StructuredFactCheckStage   → fact_check
AuthorProsodyStage         → AuthorProsodyStage
VoiceSampleCleanStage      → voicesamplecleanstage (in deadcode report path)
```

### Artifact kinds (as named in feature code, not yet formal enums)

```
text_render_quality_audit
text_render_quality_summary
voice_over_coverage_audit
voice_over_coverage_summary
text_overlay_sync_summary
voice_over_regression_gate
text_render_regression_gate
final_delivery_quality_summary
```

### Report types observed

```
.json       — machine-readable structured data
.jsonl      — machine-readable streaming/per-item data
.md         — human-readable markdown
.txt        — human-readable plain text or delimited text
.delimited.txt — delimited structured text (hybrid)
```

### File prefixes (from constants.py)

```
final_script__
provisional_script__
script_object__
outline__
authoring_output__
enrichment_output__
ideation_prompt_
segmentation_output__
render_prompt_
```

---

## Consumer-Relevant Signals

| Artifact | Useful For |
|---|---|
| `run_status.json` | OpsCenter lifecycle tracking; failure diagnosis |
| `*_topic_selection.json` | Human review; fixture harvesting |
| `*_outline__*.json` | Human review; fixture harvesting |
| `artifacts/script_contract/*/script_draft.json` | Fixture harvesting; slice replay; automated analysis |
| `*_script_segmentation.json` | Automated analysis; regression testing |
| `*_script_object__*.json` (1.5MB+) | Fixture harvesting (large; selective use) |
| `*_AuthorProsodyStage.json` | Automated analysis; regression testing; timing validation |
| `*_voice_over_asr_observations.jsonl` | ASR slice replay; timing regression testing |
| `voice_over_coverage_summary.json` | Automated gate checking; regression testing |
| `voice_over_regression_gate.json` | Automated gate checking; failure diagnosis |
| `text_render_quality_audit.jsonl` | Automated analysis; regression testing |
| `text_render_regression_gate.json` | Automated gate checking; failure diagnosis |
| `final_delivery_quality_summary.json` | Human review; automated analysis |
| `architecture_invariants/latest.json` | Architecture invariant verification; automated analysis |
| `audit/*_deadcode_report.txt` | Human review; dead code tracking |
| `*_fact_check.json` | Human review; automated analysis |

---

## Gaps / Missing Evidence

1. **No completed representative run** — the only run available is `in_progress` (interrupted during rendering). Post-run artifacts are absent.
2. **No failed run with recorded failure** — cannot directly inspect `run_status.json` with `status: failed` and `traceback` field.
3. **No enrichment run** — no evidence of what enrichment produces.
4. **No ideation run** — no evidence of what ideation produces.
5. **No render run** — no evidence of what render produces.
6. **No segmentation run** — no evidence of what segmentation produces.
7. **No stack_authoring run** — no evidence of what stack_authoring produces.
8. **text_overlay/ artifacts absent** — run was interrupted before rendering wrote these.
9. **Rendering-phase artifacts absent** — voice_over delivery, TTS wav files referenced in asr_observations.jsonl exist in `temp/` not in the run bucket.
10. **`artifact_manifest.json` does not exist** — this is a Phase 2/Phase 5 deliverable, not yet implemented.
11. **5 of 6 audit types never finalize run_status.json** — the gap between initial `in_progress` write and actual completion/failure is not bridged for non-representative audits.
12. **No bucket_reader or equivalent for non-representative audits** — `bucket_reader.py` is specific to representative audits. No equivalent discovery mechanism for other audit types.

---

## Implications for Phase 2

1. **`artifact_manifest_path` must be added to run_status.json** — it does not exist today. This is the key discovery chain link.
2. **`artifact_manifest.json` must be defined from scratch** — it does not exist in the codebase. There is no partial implementation to build on.
3. **`status` vocabulary for non-representative audits must be addressed** — Phase 2 schemas must acknowledge that non-representative audits currently only emit `in_progress` and never finalize. OpsCenter contracts must handle this.
4. **`current_phase` vocabulary is only meaningful for representative** — the other 5 types do not update this field.
5. **Path patterns are non-uniform across artifact types** — some at run_root, some under `artifacts/`, some under `artifacts/script_contract/{stage}/`, some under `audit/`. The manifest must model all of these.
6. **Architecture invariants are not run-scoped** — they have no `run_id`, no timestamp in the file itself (only in `checked_at`), and are written to a fixed path. The manifest schema must accommodate repo-level singletons as a distinct location type.
7. **Large artifacts exist** — `script_object__*.json` files are 1.5–1.6MB. The manifest must record paths, not embed content.
8. **`coverage.ini`, `.coverage.*`, `sitecustomize.py` are infrastructure noise** — they appear in the bucket but are not audit artifacts. The manifest must not include them.
9. **Bucket naming is deterministic and stable** — `{channel_slug}_{YYYYMMDD}_{HHMMSS}_{run_id}` is safe to use as a directory key.
10. **`run_id` is already in run_status.json** — OpsCenter can read it from there without scanning directory names.
11. **5 audit types need run_status.json finalization added** — Phase 5 must add `write_run_status` calls on completion and failure for enrichment, ideation, render, segmentation, and stack_authoring.

---

## Non-Decisions

The following were deliberately not decided in Phase 0:

- What the controlled vocabulary enums will be (Phase 2 deliverable)
- What the artifact manifest schema will look like (Phase 2 deliverable)
- How OpsCenter will read or index artifacts (Phase 7)
- Whether to normalize path layout across audit types (not a Phase 0 concern)
- Whether to split or merge per-run buckets (not a Phase 0 concern)
- What fixtures to harvest (Phase 9)
- Whether `text_overlay/` contents belong in the manifest (Phase 2 to decide)
- Whether `coverage.ini` / `.coverage.*` should be excluded explicitly from manifest (Phase 2 to decide)
- Whether architecture_invariants should be tracked per-run or as a singleton only (Phase 2 to decide)
- How to handle the 5 audit types that do not finalize `run_status.json` at the OpsCenter contract level (Phase 2/5 to decide)
