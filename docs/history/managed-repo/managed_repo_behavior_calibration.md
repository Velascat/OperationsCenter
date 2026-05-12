# Managed Repo Behavior Calibration

## Purpose

Behavior calibration is an evidence-based analysis layer that examines managed repo artifact indexes and produces structured findings and recommendations. It does **not** change configuration, re-run audits, or apply any automated remediation. All output is advisory and requires human review before action.

The calibration system sits entirely inside OperationsCenter. It never imports managed private project or any other managed repo code.

---

## Inputs and Outputs

### Input: `BehaviorCalibrationInput`

A dataclass that bundles the artifact index with analysis parameters:

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `repo_id` | `str` | — | Target repo identifier |
| `run_id` | `str` | — | Audit run identifier |
| `audit_type` | `str` | — | e.g. `"representative"` |
| `artifact_index` | `ManagedArtifactIndex \| None` | — | Must not be `None` at analysis time |
| `analysis_profile` | `AnalysisProfile` | — | Which rule set to apply |
| `include_artifact_content` | `bool` | `False` | Opt-in: read artifact files from disk |
| `max_artifact_bytes` | `int` | 10 MiB | Truncation cap for content reads |
| `selected_artifact_ids` | `list[str] \| None` | `None` | Restrict content analysis to specific IDs |
| `dispatch_result` | `AuditDispatchResult \| None` | `None` | Optional: wire in live dispatch context |
| `metadata` | `dict` | `{}` | Pass-through metadata |

### Output: `BehaviorCalibrationReport`

Pydantic model (serializable to JSON):

```json
{
  "schema_version": "1.0",
  "repo_id": "managed-private-project",
  "run_id": "run999",
  "audit_type": "representative",
  "analysis_profile": "summary",
  "created_at": "...",
  "artifact_index_summary": { ... },
  "findings": [ ... ],
  "recommendations": [ ... ],
  "limitations": [],
  "metadata": {}
}
```

Convenience properties: `finding_count`, `recommendation_count`, `has_errors`.

---

## Analysis Profiles

Each profile maps to a specific subset of rules:

| Profile | Purpose | Rules Applied |
|---------|---------|---------------|
| `SUMMARY` | High-level health snapshot | run_status, excluded_paths, singleton_limitations |
| `FAILURE_DIAGNOSIS` | Investigate interrupted/failed runs | run_status, partial_artifacts, missing_files |
| `COVERAGE_GAPS` | Find missing artifact categories | coverage_gaps, partial_artifacts |
| `ARTIFACT_HEALTH` | Inspect path resolution and file presence | unresolved_paths, missing_files, partial_artifacts |
| `PRODUCER_COMPLIANCE` | Check manifest metadata quality | producer_compliance |
| `RECOMMENDATION` | Full analysis with action suggestions | all rules + recommendation generation |

Only `RECOMMENDATION` produces `CalibrationRecommendation` objects. All other profiles return `recommendations=[]`.

---

## Findings

Each `CalibrationFinding` is a frozen Pydantic model with a UUID `finding_id` assigned at creation:

| Field | Type | Notes |
|-------|------|-------|
| `finding_id` | `str` | Auto-generated UUID4 |
| `severity` | `FindingSeverity` | `info`, `warning`, `error`, `critical` |
| `category` | `FindingCategory` | See table below |
| `summary` | `str` | Short human-readable description |
| `detail` | `str \| None` | Extended context |
| `artifact_ids` | `list[str]` | Affected artifact IDs |
| `source` | `str` | Rule function name |
| `confidence` | `float \| None` | 0.0–1.0, optional |
| `metadata` | `dict` | Rule-specific extras |

### Finding Categories

| Category | Meaning |
|----------|---------|
| `MISSING_ARTIFACT` | Artifact has `status=missing` in manifest |
| `PARTIAL_RUN` | Run was interrupted; artifacts may be incomplete |
| `FAILED_RUN` | Run terminated with error status |
| `UNRESOLVED_PATH` | Artifact path could not be resolved to an absolute filesystem path |
| `MISSING_FILE` | Path resolved but file is not present on disk |
| `INVALID_JSON` | File exists and is readable but fails JSON parse |
| `PRODUCER_CONTRACT_GAP` | Manifest entry is missing required metadata fields |
| `COVERAGE_GAP` | Expected artifact categories are absent |
| `RUNTIME_FAILURE` | Manifest carries error messages from the run |
| `REPO_SINGLETON_WARNING` | Singleton artifact carries overwrite-risk limitations |
| `NOISE_EXCLUSION` | Paths were excluded from the manifest (informational) |
| `UNKNOWN` | Catch-all for unexpected conditions |

---

## Recommendations

`CalibrationRecommendation` is a frozen Pydantic model. Key invariants:

- `requires_human_review` is always `True` — hardcoded, not configurable
- Recommendations are only generated when the `RECOMMENDATION` profile is selected
- Every recommendation carries `supporting_finding_ids` linking back to the findings that drove it
- Recommendations describe what to investigate or fix, never issue automated commands

| Priority | When Used |
|----------|-----------|
| `URGENT` | Critical severity findings |
| `HIGH` | Errors blocking downstream use |
| `MEDIUM` | Warnings that degrade signal quality |
| `LOW` | Informational gaps worth addressing |

---

## Content Analysis

When `include_artifact_content=True`, the `ARTIFACT_HEALTH` and `RECOMMENDATION` profiles read artifact files from disk (via Phase 7 retrieval helpers). Content analysis:

- Respects `max_artifact_bytes` (default 10 MiB) — truncates large files
- Reports `INVALID_JSON` findings for files that fail parse
- Reports `RUNTIME_FAILURE` findings for unreadable files
- Never writes to disk or modifies artifacts

Content analysis is opt-in because it requires `repo_root` to be derivable and files to exist locally.

---

## Artifact Index Summary

Every report includes an `ArtifactIndexSummary` regardless of profile:

```python
ArtifactIndexSummary(
    total_artifacts=5,
    by_kind={"stage_report": 3, "alignment_artifact": 2},
    by_location={"run_root": 4, "repo_singleton": 1},
    by_status={"present": 4, "missing": 1},
    singleton_count=1,
    partial_count=1,
    excluded_path_count=2,
    unresolved_path_count=0,
    missing_file_count=0,
    machine_readable_count=3,
    warnings_count=1,
    errors_count=0,
    manifest_limitations=["partial_run"],
)
```

---

## Report Persistence

```python
from operations_center.behavior_calibration import write_calibration_report, load_calibration_report

path = write_calibration_report(report, output_dir)
# Writes to: {output_dir}/{repo_id}/{run_id}/{profile}.json

loaded = load_calibration_report(path)
# Returns BehaviorCalibrationReport; raises FileNotFoundError if missing
```

---

## CLI

```
operations-center-calibration analyze \
    --manifest path/to/artifact_manifest.json \
    --profile summary

operations-center-calibration analyze \
    --manifest path/to/artifact_manifest.json \
    --profile recommendation \
    --include-content \
    --output-dir /tmp/reports

operations-center-calibration tune-autonomy \
    --manifest path/to/artifact_manifest.json

operations-center-calibration report \
    --path /tmp/reports/managed-private-project/run999/summary.json
```

---

## Isolation Guarantees

- **No managed repo imports**: verified by AST scan in `TestAnalyzerNonMutation.test_no_managed_repo_imports`
- **No mutation**: the analyzer never modifies the artifact index, manifest, or any files
- **No automated action**: recommendations are advisory-only; `requires_human_review=True` is a hard invariant

---

## Module Layout

```
src/operations_center/behavior_calibration/
    __init__.py          — public exports
    errors.py            — BehaviorCalibrationError hierarchy
    models.py            — all input/output Pydantic + dataclass types
    rules.py             — individual check_* rule functions
    recommendations.py   — produce_recommendations()
    analyzer.py          — analyze_artifacts() entry point
    reports.py           — write_calibration_report(), load_calibration_report()

src/operations_center/entrypoints/calibration/
    main.py              — operations-center-calibration CLI (Typer)
```
