# Manifest Wiring

How OperationsCenter picks up project + local manifests at runtime, what shows up in operator output, and how to point OC at a specific project.

For authoring the manifests themselves, see [Manifest Authoring](manifest_authoring.md).

---

## The `platform_manifest:` settings block

OC's local config (`config/operations_center.local.yaml`, gitignored) carries an optional `platform_manifest:` block that controls how the EffectiveRepoGraph is composed for this OC instance.

```yaml
platform_manifest:
  enabled: true                          # default; set false to skip composition entirely
  project_slug: my-project                # for WorkStation LocalManifest discovery
  project_manifest_path: /home/dev/Documents/GitHub/MyProject/topology/project_manifest.yaml
  local_manifest_path:   /home/dev/Documents/GitHub/MyProject/topology/local_manifest.yaml
```

| Field | Meaning |
|---|---|
| `enabled` | When false, no graph is composed (coordinator runs with `repo_graph=None`); contract-impact logging stays silent |
| `project_slug` | Used by WorkStation's `discover_local_manifest()` if `local_manifest_path` is unset; looks at `~/.config/workstation/manifests/<slug>.local.yaml` |
| `project_manifest_path` | Explicit override; takes precedence over the cwd convention |
| `local_manifest_path` | Explicit override; takes precedence over WS discovery |

**Defaults are operator-friendly:** `enabled=true`, all paths None. With nothing configured OC uses the bundled platform-only graph (9 public repos), which still drives contract-impact logging for any platform-targeted dispatch.

## Resolution order

The factory `build_effective_repo_graph_from_settings(settings, *, repo_root=None)` resolves paths in this order:

### Project layer
1. `settings.platform_manifest.project_manifest_path` (explicit)
2. `<repo_root>/topology/project_manifest.yaml` (cwd convention — when `repo_root` is provided)
3. None → platform-only graph

### Local layer
1. `settings.platform_manifest.local_manifest_path` (explicit)
2. `workstation_cli.discover_local_manifest(project_slug, repo_root=repo_root)` if WorkStation is installed and a slug is set:
   - `$WORKSTATION_LOCAL_MANIFEST` env override
   - `$XDG_CONFIG_HOME/workstation/manifests/<slug>.local.yaml`
   - `<repo_root>/topology/local_manifest.yaml`
3. None → no local annotations

The `entrypoints/execute/main.py` production CLI passes `repo_root=Path.cwd()` so the cwd convention works automatically when OC runs from inside a project repo.

## What the operator sees at dispatch time

When a dispatch targets a contract repo in the merged graph, OC emits a structured `INFO` log line:

```
INFO  contract change in CxRP affects 4 consumer(s) [public=3 private=1]: OperationsCenter, SwitchBoard, OperatorConsole, MyProjAPI
```

Plus a `contract_impact` block on the run record's observability metadata:

```python
{
  "contract_impact": {
    "target": "CxRP",
    "target_repo_id": "cxrp",
    "affected_count": 4,
    "public_affected": ["OperationsCenter", "SwitchBoard", "OperatorConsole"],
    "private_affected": ["MyProjAPI"]
  }
}
```

Dispatches against non-contract repos (leaves, project services without consumers) — silent.

## Switching projects

v0.3 of PlatformManifest deliberately supports **one project per OC instance**. To switch which project OC sees:

```yaml
# In config/operations_center.local.yaml
platform_manifest:
  project_slug: warehouse
  project_manifest_path: /home/dev/Documents/GitHub/Warehouse/topology/project_manifest.yaml
  local_manifest_path:   /home/dev/Documents/GitHub/Warehouse/topology/local_manifest.yaml
```

Or run OC from inside a project repo's working directory and let the cwd convention do the work — no settings change required.

Multi-project composition (one effective graph spanning multiple `topology/project_manifest.yaml` files) is a future PlatformManifest feature; today, it's "one OC instance, one project."

## What's gitignored vs committed

| File | Status |
|---|---|
| `config/operations_center.local.yaml` | gitignored (per-machine) |
| `config/operations_center.example.yaml` | committed (template) |
| `<project>/topology/project_manifest.yaml` | **committed** (project architecture) |
| `<project>/topology/local_manifest.example.yaml` | committed (template) |
| `<project>/topology/local_manifest.yaml` | gitignored (per-machine) |
| `~/.config/workstation/manifests/<slug>.local.yaml` | not in git at all (user config) |

## Failure modes

The factory degrades gracefully — OC startup never fails because of a manifest issue. Instead:

| Condition | Behavior |
|---|---|
| `enabled: false` | `repo_graph=None`; impact logging silent |
| Explicit `project_manifest_path` doesn't exist | Warning logged; `repo_graph=None` |
| Project manifest has schema/loader errors | Warning logged; `repo_graph=None` |
| `version_constraint` doesn't match installed PM | Warning logged; `repo_graph=None` |
| WS not installed but `project_slug` set | Debug log; local layer skipped silently |

To make these louder during ops, watch for `EffectiveRepoGraph construction failed` in the OC log. Or run a one-shot doctor command (coming in a follow-up round).

## Cross-repo task chaining (R5)

When a contract repo (CxRP/RxP/PlatformManifest, or your own contract repo) changes, downstream consumers may need re-validation runs. The `operations-center-propagate` entrypoint walks the contract-impact set and creates Plane tasks per the `contract_change_propagation:` settings block.

### Settings

```yaml
# config/operations_center.local.yaml
contract_change_propagation:
  enabled: false                          # default — nothing fires until you opt in
  auto_trigger_edge_types: []             # default — no edge types auto-fire
  dedup_window_hours: 24                  # don't re-fire same (target,consumer,version) within window
  pair_overrides: []                      # per-pair skip/backlog/ready_for_ai
  record_dir: state/propagation           # PropagationRecord artifacts land here
  dedup_path: state/propagation/dedup.json
```

### Manual trigger

```bash
operations-center-propagate \
    --target cxrp \
    --version <commit-sha> \
    --config config/operations_center.local.yaml \
    --dry-run                             # preview without hitting Plane
```

### What gets created

For each consumer in the impact set, a Plane task with:

- Title: *"Re-validate {consumer} after {target} change"*
- Body prelude with target/version/edge_type substitution
- Labels: `revalidation`, `pending-review`
- A structured `<!-- propagation:source -->` block at the bottom carrying target/version/edge_type/run_id for traceability
- State: **Backlog** (operator promotes to "Ready for AI" after triage)

### Promote a trusted pair

```yaml
contract_change_propagation:
  enabled: true
  auto_trigger_edge_types: [depends_on_contracts_from]
  pair_overrides:
    - target_repo_id: cxrp
      consumer_repo_id: operations_center
      action: ready_for_ai
      reason: trusted pair — auto-promote after CxRP change
```

### Mandatory observability

Every `propagate()` run writes a `PropagationRecord` artifact to `state/propagation/<run_id>.json` regardless of whether tasks fired. This is the audit trail for *"why did/didn't propagation fire?"* — operators always have a record without re-running.

## Related

- [Manifest Authoring](manifest_authoring.md) — what to put in `topology/project_manifest.yaml`
- [Runtime Settings](runtime.md) — broader OC config reference
- PlatformManifest [v0.4.0 release](https://github.com/Velascat/PlatformManifest/releases/tag/v0.4.0)
