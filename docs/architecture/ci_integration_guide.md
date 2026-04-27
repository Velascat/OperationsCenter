# CI Integration Guide — Managed Repo Audit System

This guide describes how to wire the OperationsCenter governance flow into a CI/CD pipeline as a pre-release gate.

---

## Prerequisites

1. The managed repo (e.g. VideoFoundry) is configured in `config/managed_repos/videofoundry.yaml`.
2. A `GovernanceConfig` state directory exists and is writable by the CI agent.
3. The CI agent has write access to an output directory for fixture packs and reports.
4. A mini regression suite definition (`suite.json`) for the target repo/audit type is available on disk.

---

## Governance Flow

The correct production path is:

```
Phase 11: operations-center-regression run   ← fast; no dispatch; must pass first
Phase 12: operations-center-governance run   ← triggers live audit via dispatch
```

`operations-center-governance run` enforces `mini_regression_first` policy for `urgency=low` and `urgency=normal` runs. The governance check will block dispatch and require manual approval if no Phase 11 evidence is supplied.

---

## Minimal CI Pipeline (GitHub Actions example)

```yaml
jobs:
  audit-gate:
    runs-on: ubuntu-latest
    env:
      OC_STATE_DIR: /tmp/oc-state
      OC_OUTPUT_DIR: /tmp/oc-output
      OC_SUITE_PATH: suites/videofoundry_representative.json
    steps:
      - uses: actions/checkout@v4

      - name: Install OperationsCenter
        run: pip install -e .

      - name: Run mini regression suite (Phase 11)
        run: |
          operations-center-regression run \
            --suite-path "$OC_SUITE_PATH" \
            --output-dir "$OC_OUTPUT_DIR/regression"
        # Exits non-zero if any required entry fails.

      - name: Run governed audit (Phase 12)
        run: |
          operations-center-governance run \
            --repo-id videofoundry \
            --audit-type representative \
            --requested-by "ci/${{ github.actor }}" \
            --related-suite-report-path "$OC_OUTPUT_DIR/regression/videofoundry_representative/$(ls $OC_OUTPUT_DIR/regression/videofoundry_representative | tail -1)/suite_report.json" \
            --state-dir "$OC_STATE_DIR"
        # Exits non-zero if governance denies, defers, or dispatch fails.
```

---

## Exit Codes

| Exit code | Meaning |
|-----------|---------|
| `0` | Command succeeded (suite passed / audit dispatched and completed) |
| `1` | Command failed (suite required entry failed / governance denied / dispatch error) |

Both `operations-center-regression` and `operations-center-governance` follow standard POSIX exit code conventions.

---

## Urgency and Approval

| `--urgency` | `mini_regression_first` enforced? | Manual approval required? |
|-------------|-----------------------------------|--------------------------|
| `low` (default) | Yes — suite report path required | No (if suite passes) |
| `normal` | Yes | No (if suite passes) |
| `high` | No | **Yes — always** |
| `urgent` | No | **Yes — always** |

For `high`/`urgent` runs, pass `--approval-token <token>` or configure an out-of-band approval record via `operations-center-governance approve`.

---

## Cooldown and Budget

Governance enforces per-repo cooldown and per-period budget limits configured in `GovernanceConfig`:

```yaml
# config/operations_center.local.yaml (example)
governance:
  known_repos:
    - videofoundry
  known_audit_types:
    videofoundry:
      - representative
      - enrichment
  cooldown_seconds: 3600      # 1 hour minimum between runs
  budget_runs_per_period: 10  # max 10 runs per period
  budget_period_days: 7
  state_dir: /var/lib/oc-state
```

If the cooldown or budget is exhausted, `operations-center-governance run` exits `1` with `governance_status=denied`. The CI job should treat this as a soft block and surface the reason to the operator.

---

## State Directory

Budget and cooldown state is persisted in `--state-dir` via `fcntl`-locked JSON files (Linux/macOS only). In ephemeral CI environments, mount a persistent volume or remote filesystem at this path so state survives across runs. Without persistence, cooldown and budget state resets on every job.

---

## Governance Report

On completion, `operations-center-governance run` writes a `governance_report.json` to the state directory. This file contains:

- `governance_status`: one of `approved_and_dispatched`, `needs_manual_approval`, `denied`, `deferred`, `dispatch_failed`
- `dispatched_run_id`: the `AUDIT_RUN_ID` injected into the managed repo subprocess (only present when dispatched)
- `related_suite_report_path`: the Phase 11 evidence path recorded in the audit trail

Capture this file as a CI artifact for traceability:

```yaml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: governance-report
          path: /tmp/oc-state/governance_report.json
```

---

## Lockdown Reminder

These rules apply in CI as in any other context:

```
No new CI step may bypass run_status.json discovery.
No CI step may call dispatch without governance (use operations-center-governance, not operations-center-audit).
No Phase 11 suite run may call dispatch.
```

The `operations-center-audit run` entrypoint bypasses all governance checks and is documented as a development escape hatch — it must not appear in production CI pipelines.
