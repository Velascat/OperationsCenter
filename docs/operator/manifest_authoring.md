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
pip install "platform-manifest @ git+https://github.com/Velascat/PlatformManifest.git@v0.4.0"

platform-manifest validate topology/project_manifest.yaml --expected project
```

Two-stage check (JSON Schema + Python loader); exit `0` clean, `1` validation failed. Set up a `manifest-validate.yml` GitHub Actions workflow per the pattern in [VideoFoundry](https://github.com/Velascat/VideoFoundry/blob/main/.github/workflows/manifest-validate.yml) so PRs catch drift before merge.

## Multi-repo project — shell pattern (PM v0.8+)

When several repos together form one logical project, use a **shell manifest repo**. Each constituent repo keeps its own `topology/project_manifest.yaml`; one dedicated shell repo includes all of them.

### When to use a shell repo

- ✅ Two or more repos collaborate as one product (e.g. `VideoFoundryAPI` + `VideoFoundryWorker` + `VideoFoundryAssets`)
- ✅ You want OperationsCenter to see all of them in one merged graph
- ✅ Cross-repo edges (`repo A bundles_assets_from repo B`) span the suite
- ❌ Two repos happen to coexist but aren't logically one project (each gets its own standalone manifest)

### Shape

```
VideoFoundrySuite/                          # the dedicated shell repo
  topology/
    project_manifest.yaml                   # references all sub-projects
    local_manifest.example.yaml             # per-machine wiring for the whole suite
    local_manifest.yaml                     # gitignored

VideoFoundry/                               # constituent repos keep their own manifests
  topology/
    project_manifest.yaml

Warehouse/
  topology/
    project_manifest.yaml
```

### Shell manifest example

```yaml
manifest_kind: project
manifest_version: "1.0.0"

platform_manifest:
  name: PlatformManifest
  version_constraint: ">=0.8,<1.0"

# Order matters: included manifests apply in sequence. Earlier includes
# may be referenced by later ones via canonical name.
includes:
  - name: VideoFoundry
    project_manifest_path: ../VideoFoundry/topology/project_manifest.yaml
  - name: Warehouse
    project_manifest_path: ../Warehouse/topology/project_manifest.yaml

# The shell may also declare its own repos (rare — usually empty).
repos: {}

# Cross-suite edges that don't belong in any constituent's manifest
# go here. Example: a deployment-pipeline repo that orchestrates both
# VFA and Warehouse.
edges: []
```

### Composition rules across the shell

The loader enforces, in addition to the single-project rules:

| Rule | Behavior on violation |
|---|---|
| Two sub-projects declare the same `repo_id` | Hard fail — `'X' already declared by an included sub-project` |
| Sub-project tries to redefine a platform `repo_id` | Hard fail — same as single-project case |
| Cycle (A includes B includes A) | Hard fail — `cycle detected` |
| Excessive nesting (>4 deep by default) | Hard fail — `depth exceeded` |
| Sub-project edge between two platform nodes | Hard fail — sub-projects can't reshape platform's internal graph |
| Sub-project edge to/from a sibling sub-project | **Allowed** — this is the whole point |

### Pointing OC at a shell

```yaml
# config/operations_center.local.yaml
platform_manifest:
  project_slug: video-foundry-suite
  project_manifest_path: ../VideoFoundrySuite/topology/project_manifest.yaml
  local_manifest_path:   ../VideoFoundrySuite/topology/local_manifest.yaml
```

The merged graph contains all constituent repos' nodes, all their edges, plus the shell's own additions. OC's contract-impact analysis spans the whole suite.

### Why not "just declare all repos in one big manifest"?

- **Auditable architecture**: each constituent repo has its own manifest under its own version control. The shell expresses *composition*, not duplication.
- **Visibility never widens**: a private node from sub-project A stays private after the shell merges it.
- **Independent evolution**: VF can update its own manifest without touching Warehouse's; the shell picks up the latest of each on next compose.

## Worked examples

- **Single-repo private project:** [VideoFoundry/topology/project_manifest.yaml](https://github.com/Velascat/VideoFoundry/blob/main/topology/project_manifest.yaml)
- **Single-repo private tool:** [Warehouse/topology/project_manifest.yaml](https://github.com/Velascat/Warehouse/blob/main/topology/project_manifest.yaml)

Both follow the same shape: one repo node, `runtime_role: managed_repo`, one `OperationsCenter dispatches_to <repo>` edge for the audit/maintenance pattern.

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
