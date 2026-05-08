# Post-merge auto-trigger for cross-repo task chaining (R5.4)

When a contract repo (CxRP, RxP, PlatformManifest, or your own contract repo) merges to `main`, you can wire a GitHub Actions workflow that automatically invokes `operations-center-propagate` against an OC instance. This eliminates the manual run after every contract change.

> **Trust posture (default):** the workflow runs with `--require-enabled` against an OC config that has `contract_change_propagation.enabled: true`. Tasks land in **Backlog** unless a `pair_overrides` entry promotes the pair. Operator promotes after triage. This is the safe default — turn auto-promotion on per-pair only after you trust the cycle.

## Reference workflow

Place this in the **contract repo's** `.github/workflows/propagate.yml` (e.g. on CxRP, RxP, or PlatformManifest):

```yaml
name: Propagate contract change

on:
  push:
    branches: [main]

jobs:
  propagate:
    name: Notify downstream consumers
    runs-on: ubuntu-latest
    # Only fire for non-fork pushes — avoids accidental cross-org spam
    if: github.event.repository.fork == false
    steps:
      - name: Checkout OperationsCenter
        uses: actions/checkout@v4
        with:
          repository: Velascat/OperationsCenter
          token: ${{ secrets.OC_REPO_TOKEN }}    # PAT with repo read
          path: oc

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install OperationsCenter
        working-directory: oc
        run: pip install -e .

      - name: Stage OC config
        env:
          PLANE_API_TOKEN: ${{ secrets.PLANE_API_TOKEN }}
          OC_PROPAGATE_CONFIG: ${{ secrets.OC_PROPAGATE_CONFIG }}
        run: |
          # The config secret carries a YAML with contract_change_propagation
          # ENABLED + auto_trigger_edge_types set. See template below.
          mkdir -p oc/config
          printf '%s' "$OC_PROPAGATE_CONFIG" > oc/config/operations_center.local.yaml

      - name: Propagate
        working-directory: oc
        env:
          PLANE_API_TOKEN: ${{ secrets.PLANE_API_TOKEN }}
        run: |
          operations-center-propagate \
            --target ${{ github.event.repository.name }} \
            --version ${{ github.sha }} \
            --config config/operations_center.local.yaml \
            --require-enabled \
            --json
```

## Required secrets

Set these on the contract repo (Settings → Secrets and variables → Actions):

| Secret | Purpose |
|---|---|
| `OC_REPO_TOKEN` | GitHub PAT with `repo` scope; lets the workflow check out OperationsCenter |
| `PLANE_API_TOKEN` | The Plane API token OC uses to create issues |
| `OC_PROPAGATE_CONFIG` | Full YAML config for OC. **At minimum** must include `plane:`, `git:`, `kodo:`, `repos:`, and a `contract_change_propagation:` block with `enabled: true` |

## Minimum OC_PROPAGATE_CONFIG

```yaml
plane:
  base_url: https://your-plane.example.com
  api_token_env: PLANE_API_TOKEN
  workspace_slug: your-workspace
  project_id: <plane-project-uuid>

git:
  token_env: GITHUB_TOKEN

kodo:
  binary: kodo

repos: {}    # propagator doesn't need any repos defined

platform_manifest:
  enabled: true
  # Operator: point at a project manifest if you want private project
  # consumers to be reached by propagation. Leaving these unset means
  # only public platform consumers (OC, SB, OperatorConsole) get tasks.

contract_change_propagation:
  enabled: true
  auto_trigger_edge_types: [depends_on_contracts_from]
  dedup_window_hours: 24
  # Per-pair trust: auto-promote OC after CxRP changes
  pair_overrides:
    - target_repo_id: cxrp
      consumer_repo_id: operations_center
      action: ready_for_ai
      reason: trusted pair — re-validation always wanted
```

## What the workflow produces

For each merged commit on the contract repo:

1. The workflow checks out OC, installs it, stages the config from secrets.
2. `operations-center-propagate` walks the contract-impact set for the target.
3. Per the policy:
   - Disabled pair → no task (recorded as skip in `PropagationRecord`).
   - Enabled pair → Plane task created in **Backlog** with the parent-link block.
   - Promoted pair → same, then transitioned to "Ready for AI".
4. A `PropagationRecord` JSON artifact lands in the workflow runner's filesystem (under `state/propagation/<run_id>.json`). Pin it as a workflow artifact if you want it preserved beyond the runner's lifetime.
5. Dedup: same `(target, consumer, version)` won't re-fire within 24h.

## Pinning artifacts

To preserve `PropagationRecord` JSON in CI for after-the-fact inspection:

```yaml
      - name: Upload propagation records
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: propagation-records-${{ github.sha }}
          path: oc/state/propagation/*.json
          retention-days: 90
```

## Verifying the chain

After a propagation fires, inspect parent-child links from your local machine:

```bash
operations-center-propagation-links list
operations-center-propagation-links latest --target cxrp
operations-center-propagation-links show <run_id>
```

The CLI reads `state/propagation/*.json` and reports `target → consumers` chains for any propagation run.

## Things to watch

- **Plane rate limit** — the workflow batches sequentially with no sleep; if you have >20 consumers, add a Plane-rate-limit-aware shim or batch.
- **Contract repo forks** — `github.event.repository.fork == false` prevents external forks from firing into your Plane.
- **Token scope** — `OC_REPO_TOKEN` only needs read on OC; `PLANE_API_TOKEN` needs write on the Plane project. Don't reuse a high-privilege token for both.
- **Secret rotation** — when you rotate `OC_PROPAGATE_CONFIG`, the next push picks up the new config. Test with a `--dry-run` step before flipping `--require-enabled`.

## Disabling

Two clean off-switches:

1. Flip `contract_change_propagation.enabled: false` in `OC_PROPAGATE_CONFIG` — the workflow exits 1 on `--require-enabled` so propagation can't accidentally fire.
2. Disable the workflow itself in the contract repo's Actions settings.
