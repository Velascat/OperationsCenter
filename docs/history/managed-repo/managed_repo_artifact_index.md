# Managed Repo Artifact Index

**Phase**: 7  
**Package**: `src/operations_center/artifact_index/`  
**CLI**: `operations-center-artifacts`

---

## Purpose

Phase 7 makes managed audit artifacts queryable from within OperationsCenter.

The goal is to load a contract-compliant `artifact_manifest.json`, build a normalized in-memory index of artifact entries, and retrieve artifacts by stable criteria.

Phase 7 does **not** harvest fixtures, implement slice replay testing, change managed private project output behavior, or scan directories to discover artifacts. The manifest is the only source of truth.

---

## Relationship to Phase 2 Artifact Contract

Phase 2 (`src/operations_center/audit_contracts/`) defines:
- `ManagedArtifactManifest` — the schema for `artifact_manifest.json`.
- `ManagedArtifactEntry` — individual artifact entries within the manifest.
- `ExcludedPath` — infrastructure noise records excluded from artifact entries.
- `Location`, `PathRole`, `ArtifactStatus`, `ConsumerType`, `ValidFor`, `Limitation` — controlled vocabulary.

Phase 7 consumes these types directly. It does not redefine or modify the Phase 2 schema.

---

## Relationship to Phase 6 Dispatch Results

Phase 6 (`src/operations_center/audit_dispatch/`) produces a `ManagedAuditDispatchResult` with `artifact_manifest_path` — the resolved absolute path to `artifact_manifest.json`.

Phase 7 provides `index_dispatch_result(result)` as a convenience entry point:

```
dispatch_managed_audit(request)
  → ManagedAuditDispatchResult
      artifact_manifest_path
        → load_artifact_manifest(path)
        → build_artifact_index(manifest, path)
        → ManagedArtifactIndex
```

---

## Manifest as Source of Truth

Phase 7 never:
- Scans output directories to discover artifacts.
- Infers artifact paths outside the manifest.
- Reads stdout/stderr to find artifact paths.
- Treats excluded_paths as artifacts.

Every `IndexedArtifact` in the index was declared in the manifest. No artifact is created by discovery.

---

## Index Model

```
ArtifactIndexSource
  manifest_path, repo_id, run_id, audit_type, producer

ManagedArtifactIndex
  source: ArtifactIndexSource
  manifest_status, run_status
  artifact_root, run_root
  artifacts: list[IndexedArtifact]
  excluded_paths: list[ExcludedPath]   ← not artifacts, preserved separately
  warnings, errors, limitations, metadata

IndexedArtifact
  ← all manifest entry fields (verbatim)
  + resolved_path: Path | None         ← OpsCenter-derived
  + exists_on_disk: bool | None        ← OpsCenter-derived
  + is_repo_singleton: bool            ← OpsCenter-derived
  + is_partial: bool                   ← OpsCenter-derived
  + is_machine_readable: bool          ← OpsCenter-derived

ArtifactQuery
  ← optional filter fields (all None = no filter)
```

Derived fields do not mutate the source manifest.

---

## Path Resolution Rules

Artifact paths in the manifest may be absolute or relative. Phase 7 resolves them as follows:

| Case | Resolution |
|------|-----------|
| Absolute path | Used as-is. |
| `EXTERNAL_OR_UNKNOWN` location | Not resolved — `resolved_path = None`. |
| Relative path, `repo_root` provided | `(repo_root / path).resolve()` |
| Relative path, no `repo_root` | Derive root from manifest position + `run_root` depth. |
| Derivation fails | `resolved_path = None`. |

**Derivation heuristic**: the manifest file lives at `{repo_root}/{run_root}/artifact_manifest.json`. The builder steps up `len(run_root.parts)` parent directories from `manifest_dir` to obtain `repo_root`. This works for standard VF bucket layouts.

If a path cannot be safely resolved, it is marked `resolved_path = None` and `exists_on_disk = None`. The caller receives a clear signal rather than a guess.

---

## Query API

```python
query_artifacts(index, query) -> list[IndexedArtifact]
```

Supported filters (all optional):

| Field | Match type |
|-------|-----------|
| `repo_id` | exact |
| `run_id` | exact |
| `audit_type` | exact |
| `artifact_kind` | exact |
| `location` | exact enum |
| `path_role` | exact enum |
| `source_stage` | exact |
| `status` | exact enum |
| `consumer_type` | membership (artifact must include this value) |
| `valid_for` | membership |
| `limitation` | membership |
| `content_type` | exact |
| `exists_on_disk` | bool |
| `is_repo_singleton` | bool |
| `is_partial` | bool |

An empty or `None` query returns all artifacts. Querying never crawls directories.

---

## Retrieval API

```python
get_artifact_by_id(index, artifact_id) -> IndexedArtifact
# Raises ArtifactNotFoundError if not found.

resolve_artifact_path(index, artifact_id) -> Path
# Raises ArtifactNotFoundError or ArtifactPathUnresolvableError.

read_text_artifact(index, artifact_id, *, max_bytes=10_485_760) -> str
# Reads content. Raises on unresolvable path or unreadable file.

read_json_artifact(index, artifact_id, *, max_bytes=10_485_760) -> Any
# Reads and parses JSON. Raises ManifestInvalidError on bad JSON.
```

All retrieval operations use the indexed path. No directory search is performed. Reading file contents is size-guarded (default 10 MiB).

---

## Excluded Paths Handling

`excluded_paths` from the manifest (infrastructure noise: `coverage.ini`, `.coverage.*`, `sitecustomize.py`) are preserved at index level as `ManagedArtifactIndex.excluded_paths`.

They are **never** included in `index.artifacts`. Queries over artifacts will never return excluded paths. An optional helper can list them separately:

```python
index.excluded_paths   # list[ExcludedPath]
```

---

## Repo Singleton Handling

Artifacts with `location=REPO_SINGLETON` (e.g. architecture invariant scan) are included in the index and marked `is_repo_singleton = True`. They are not duplicated into run-root locations.

Their `valid_for` and `limitations` from the manifest are preserved verbatim. Path resolution follows the same rules as other relative paths (against `repo_root`).

They can be queried separately:

```python
index.singleton_artifacts          # list[IndexedArtifact]
query_artifacts(index, ArtifactQuery(is_repo_singleton=True))
```

---

## Dispatch Result Integration

```python
from operations_center.artifact_index import index_dispatch_result

result = dispatch_managed_audit(request)
index = index_dispatch_result(result)
```

`index_dispatch_result(result)` requires `result.artifact_manifest_path` to be set. It raises `NoManifestPathError` otherwise. It does not rerun dispatch, inspect stdout/stderr, or scan directories.

An optional `repo_root` keyword argument can be forwarded for path resolution.

---

## CLI / Tool Entry Point

```
operations-center-artifacts index --manifest <artifact_manifest.json>
operations-center-artifacts list  --manifest <artifact_manifest.json>
operations-center-artifacts get   --manifest <artifact_manifest.json> --artifact-id <id>
operations-center-artifacts query --manifest <artifact_manifest.json> --kind <kind>
```

The CLI is read-only. It does not run audits, modify manifests, scan directories, harvest fixtures, run slice tests, or import managed repo code.

Exit codes: `0` success, `1` manifest not found, `2` invalid manifest, `3` invalid filter value.

---

## Non-Goals

Phase 7 explicitly does not implement:

- **Fixture harvesting** — artifact content is not extracted or transformed into fixtures.
- **Slice replay testing** — no replay infrastructure.
- **Regression suites** — no test generation.
- **Directory scanning** — the only file reads are the manifest and optionally artifact content (via explicit `read_*` calls).
- **Persistent index storage** — the index is in-memory only.
- **managed private project code imports** — hard boundary is enforced and verified by AST test.
- **Index invalidation or caching** — callers rebuild the index from the manifest each time.

---

## Acceptance Criteria

```
[x] OperationsCenter can load a known artifact_manifest.json.
[x] OperationsCenter can build a ManagedArtifactIndex from a validated manifest.
[x] Index preserves manifest-level context (repo_id, run_id, audit_type, status).
[x] Index contains all normal artifacts from the manifest.
[x] Index includes repo_singleton artifacts distinctly.
[x] Index preserves partial/failed artifact metadata.
[x] Index preserves excluded_paths outside artifact entries.
[x] Artifacts can be queried by kind/location/stage/consumer/valid_for/limitations.
[x] Artifact lookup by artifact_id works.
[x] Artifact path resolution uses manifest/index data only.
[x] Optional text/JSON artifact reading is safe and guarded.
[x] Dispatch result integration can index from artifact_manifest_path.
[x] No directory scanning is introduced.
[x] No managed private project code is imported.
[x] Tests cover completed and failed manifests.
[x] Docs explain manifest-as-source-of-truth behavior.
```
