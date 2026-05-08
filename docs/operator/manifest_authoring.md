# Manifest Authoring

How to author a `topology/project_manifest.yaml` for a project repo.

PlatformManifest's 3-layer design separates public platform repos from private project repos from machine-local wiring. This page covers the **project layer**: how a single project (private or public) declares itself and attaches to the platform.

For the design rationale, see PlatformManifest's [README](https://github.com/Velascat/PlatformManifest/blob/main/README.md).

---

## Quick start

For a single-repo project (one repo = one project):

```
ProjectRepo/
  topology/
    project_manifest.yaml          # committed
    local_manifest.example.yaml    # committed (template)
    local_manifest.yaml            # gitignored (per-machine)
```

For a multi-repo project, put `topology/` in a dedicated project shell repo (e.g. `MyProductManifest`) and leave the consumer repos alone.

## Minimum viable project manifest

```yaml
manifest_kind: project
manifest_version: "1.0.0"

# Pin the public PlatformManifest API contract this manifest depends on.
# Bump deliberately when PM majors — never silently widen.
platform_manifest:
  name: PlatformManifest
  version_constraint: ">=0.4,<1.0"

repos:
  myproj_api:
    canonical_name: MyProjAPI
    visibility: private          # or public if open-source
    github_url: https://github.com/<org>/<repo>
    runtime_role: project_service

edges:
  # Project repos may reference platform nodes by canonical name.
  - {from: MyProjAPI, to: OperationsCenter, type: dispatches_to}
```

## Required fields

Every project manifest must carry:

| Field | Rule |
|---|---|
| `manifest_kind: project` | Loader rejects mismatched slot |
| `manifest_version` | PEP 440 — independent from PlatformManifest version |
| Each node's `canonical_name` | Non-empty string |
| Each node's `visibility` | `public` or `private` (loader-enforced) |

## Edge vocabulary (frozen at v1)

| Edge type | Meaning |
|---|---|
| `depends_on_contracts_from` | This node consumes contracts owned by the target |
| `dispatches_to` | This node hands work off to the target |
| `routes_through` | This node uses the target as a router/lane selector |

Adding a new edge type is a PlatformManifest minor version bump and requires a real consumer query that needs it.

## Merge rules — what project manifests CAN do

- ✅ Add new project nodes (public or private)
- ✅ Add edges between project nodes
- ✅ Add edges from project nodes to platform nodes (e.g. `MyProjAPI → CxRP`)
- ✅ Add edges from platform nodes to project nodes when the project is the target of platform dispatch (e.g. `OperationsCenter → MyProjAPI` for managed-repo audit runs)

## Merge rules — what project manifests CANNOT do

- ❌ Redefine platform repo_ids (loader rejects the collision)
- ❌ Add edges where both endpoints are platform nodes (the platform owns its own internal graph)
- ❌ Use any of the 7 LocalManifest-only fields (`local_path`, `local_port`, `env_file`, `endpoint_override`, `cache_path`, `gpu_required`, `runtime_hints`) — those go in `local_manifest.yaml`, never in the committed project manifest

## Visibility — when to mark `private`

- **`public`** — repo is open-source (e.g. a future cross-customer SDK)
- **`private`** — anything closed-source, customer-specific, or operationally sensitive

Private nodes never leak into PlatformManifest's bundled YAML — the loader rejects them at the platform-base layer. Project layers may carry private nodes freely; that's the whole point.

## Validate before committing

```bash
pip install "platform-manifest @ git+https://github.com/Velascat/PlatformManifest.git@v0.9.0"

platform-manifest validate topology/project_manifest.yaml   --expected project
platform-manifest validate topology/work_scope_manifest.yaml --expected work_scope
```

Two-stage check (JSON Schema + Python loader); exit `0` clean, `1` validation failed. Set up a `manifest-validate.yml` GitHub Actions workflow that runs the same command on every PR so drift is caught before merge.

## Multi-repo work scopes — `WorkScopeManifest` (PM v0.9+)

When several repos together form one OperationsCenter work scope, author a **WorkScopeManifest** (`manifest_kind: work_scope`) in a dedicated shell repo. Each constituent repo keeps its own `topology/project_manifest.yaml`; one work-scope manifest composes them.

> **Migrating from the v0.8 project-shell pattern**: `manifest_kind: project` with `includes:` is deprecated as of PM v0.9.0 (still loads with a `DeprecationWarning`) and will hard-fail in PM v1.0.0. Migration is a one-line change: `manifest_kind: project` → `manifest_kind: work_scope`. The `includes:` shape is unchanged. See [migration](#migration-from-v08-project-shell-style) below.

### When to author a `WorkScopeManifest`

- ✅ Two or more repos collaborate as one product (e.g. `MediaProductAPI` + `MediaProductWorker` + `MediaProductAssets`)
- ✅ You want OperationsCenter to see all of them in one merged graph
- ✅ Cross-repo edges (`repo A bundles_assets_from repo B`) span the suite
- ❌ Two repos happen to coexist but aren't logically one work scope (each gets its own standalone `ProjectManifest`)

### Shape

```
MediaProductSuite/                          # the dedicated shell repo
  topology/
    work_scope_manifest.yaml                # composes all constituent ProjectManifests
    local_manifest.example.yaml             # per-machine wiring for the whole suite
    local_manifest.yaml                     # gitignored

MediaProductCore/                           # each constituent keeps its own ProjectManifest
  topology/
    project_manifest.yaml

MediaProductAssets/
  topology/
    project_manifest.yaml
```

### `WorkScopeManifest` example

```yaml
manifest_kind: work_scope
manifest_version: "1.0.0"

platform_manifest:
  name: PlatformManifest
  version_constraint: ">=0.9,<1.0"

# Required. Order matters — earlier includes may be referenced by later
# ones via canonical name.
includes:
  - name: MediaProductCore
    project_manifest_path: ../MediaProductCore/topology/project_manifest.yaml
  - name: MediaProductAssets
    project_manifest_path: ../MediaProductAssets/topology/project_manifest.yaml

# Optional. Work-scope-level repos rarely needed — usually empty.
repos: {}

# Cross-suite edges that don't belong in any constituent's manifest go
# here. Carry Source.WORK_SCOPE provenance, distinguishing them from
# project-internal edges in impact analyses.
edges: []
```

### Composition rules

The loader enforces, in addition to the single-project rules:

| Rule | Behavior on violation |
|---|---|
| Two included projects declare the same `repo_id` | Hard fail — `'X' already declared by an included project` |
| Included project tries to redefine a platform `repo_id` | Hard fail |
| Work-scope manifest tries to redefine a platform `repo_id` | Hard fail |
| Work-scope edge between two platform nodes | Hard fail |
| Cycle (A includes B includes A) | Hard fail |
| Excessive nesting (>4 deep by default) | Hard fail |
| Edge to/from a sibling included project | **Allowed** — the whole point |

### Pointing OC at a `WorkScopeManifest`

```yaml
# config/operations_center.local.yaml
platform_manifest:
  project_slug: media-product-suite
  # Use exactly one of project_manifest_path / work_scope_manifest_path:
  work_scope_manifest_path: ../MediaProductSuite/topology/work_scope_manifest.yaml
  local_manifest_path:      ../MediaProductSuite/topology/local_manifest.yaml
```

Setting both `project_manifest_path` and `work_scope_manifest_path` is a configuration error — OC's settings layer enforces XOR at config load. Run `operations-center-graph-doctor` to confirm `mode: work_scope` is reported.

The merged graph contains all included projects' nodes + edges, plus the work scope's own additions (Source.WORK_SCOPE). OC's contract-impact analysis spans the whole suite.

### Migration from v0.8 project-shell style

If you have a manifest authored under PM v0.8.x using `manifest_kind: project` with `includes:`:

```diff
- manifest_kind: project
+ manifest_kind: work_scope
  manifest_version: "1.0.0"
  platform_manifest:
    name: PlatformManifest
-   version_constraint: ">=0.8,<1.0"
+   version_constraint: ">=0.9,<1.0"
  includes:
    - name: ...
      project_manifest_path: ...
```

And in your OC config:

```diff
  platform_manifest:
    project_slug: media-product-suite
-   project_manifest_path: ../MediaProductSuite/topology/project_manifest.yaml
+   work_scope_manifest_path: ../MediaProductSuite/topology/work_scope_manifest.yaml
    local_manifest_path: ../MediaProductSuite/topology/local_manifest.yaml
```

(Optionally rename `project_manifest.yaml` → `work_scope_manifest.yaml` to match the new vocabulary; only the manifest's `manifest_kind` field is load-bearing.)

### Why not "just declare all repos in one big manifest"?

- **Auditable architecture**: each constituent repo has its own manifest under its own version control. The shell expresses *composition*, not duplication.
- **Visibility never widens**: a private node from sub-project A stays private after the shell merges it.
- **Independent evolution**: each constituent updates its own manifest without touching the others; the shell picks up the latest of each on next compose.

## Worked examples

- **Single-repo private project:** see your operator-private overlay at `config/managed_repos/local/<repo_id>.yaml` for the binding shape; the topology manifest in the bound repo follows the same canonical layout.

The standard shape is: one repo node, `runtime_role: managed_repo`, one `OperationsCenter dispatches_to <repo>` edge for the audit/maintenance pattern.

## Common mistakes

| Mistake | Loader error |
|---|---|
| Missing `visibility` field | `repo '<id>' missing required 'visibility' field` |
| Local field in project manifest | `repo '<id>' has local-only field(s) [...] in a project manifest` |
| Edge to unknown node | `edge #N 'to' unknown: <name>` |
| Two repos sharing a legacy alias | `name '<alias>' maps to both '<a>' and '<b>'` |
| `manifest_kind: platform` in a project file | `expected manifest_kind='project'; got manifest_kind='platform'` |

All caught by `platform-manifest validate` at PR time.

## Related

- [Manifest Wiring](manifest_wiring.md) — how OperationsCenter picks up your project manifest at runtime
- [PlatformManifest design doc](https://github.com/Velascat/PlatformManifest/blob/main/README.md)
