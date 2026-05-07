# OperationsCenter Documentation

Index for the `docs/` tree. The README at the repo root covers OC's primary
operator model and execution boundary; this directory holds OC-internal
architecture, design notes, operator how-tos, and historical material.

The cross-repo platform architecture (ownership, contracts, routing, backend
adapters, policy guardrails) lives in [WorkStation/docs/architecture/](https://github.com/Velascat/WorkStation/tree/main/docs/architecture).
This directory holds OC-specific material.

## Operator

- [operator/setup.md](operator/setup.md) — First-time setup and config.
- [operator/runtime.md](operator/runtime.md) — Day-to-day runtime usage.
- [operator/diagnostics.md](operator/diagnostics.md) — How to inspect a run.
- [operator/tuning.md](operator/tuning.md) — Threshold and policy tuning.
- [operator/pr_review.md](operator/pr_review.md) — PR-review worker usage.
- [operator/run-artifacts.md](operator/run-artifacts.md) — Layout and contents of
  `~/.console/operations_center/runs/<run_id>/`.
- [operator/switchboard.md](operator/switchboard.md) — Operating against
  SwitchBoard from OC.
- [operator/weekly_audits.md](operator/weekly_audits.md) — Weekly audit cadence.

## Backends

- [backends/aider_local.md](backends/aider_local.md) — `aider_local` backend
  adapter behaviour and config.

## Architecture (OC-specific)

- [architecture/audit/audit_architecture.md](architecture/audit/audit_architecture.md) —
  Audit-dispatch architecture inside OC.
- [architecture/audit/audit_triage_plan.md](architecture/audit/audit_triage_plan.md)
- [architecture/policy/anti_collapse_invariant.md](architecture/policy/anti_collapse_invariant.md)
- [architecture/audit/backend_control_audit.md](architecture/audit/backend_control_audit.md)
- [architecture/ci/ci_integration_guide.md](architecture/ci/ci_integration_guide.md)
- [architecture/audit/code_health_audit.md](architecture/audit/code_health_audit.md)
- [architecture/contracts/contract-map.md](architecture/contracts/contract-map.md)
- [architecture/contracts/execution-handoff-cutover.md](architecture/contracts/execution-handoff-cutover.md)
- [architecture/contracts/execution_target.md](architecture/contracts/execution_target.md)
- [architecture/contracts/lifecycle_labels.md](architecture/contracts/lifecycle_labels.md)
- [architecture/recovery/phantom_helper_waves.md](architecture/recovery/phantom_helper_waves.md)
- [architecture/policy/policy-pre-execution-gate.md](architecture/policy/policy-pre-execution-gate.md)
- [architecture/recovery/recovery_loop_design.md](architecture/recovery/recovery_loop_design.md)
- [architecture/routing/routing-contract-fidelity.md](architecture/routing/routing-contract-fidelity.md)
- [architecture/adr/](architecture/adr/) — OC architecture decision records.

### VideoFoundry-specific

- [architecture/videofoundry/videofoundry_managed_repo_contract.md](architecture/videofoundry/videofoundry_managed_repo_contract.md)
- [architecture/videofoundry/videofoundry_audit_artifact_contract.md](architecture/videofoundry/videofoundry_audit_artifact_contract.md)
- [architecture/videofoundry/videofoundry_audit_ground_truth.md](architecture/videofoundry/videofoundry_audit_ground_truth.md)

> **Tuning and upstream-patch evaluation** (`routing-tuning.md`,
> `upstream-patch-evaluation.md` and their examples) are now sourced from
> WorkStation: see [WorkStation/docs/architecture/](https://github.com/Velascat/WorkStation/tree/main/docs/architecture).

## Design

- [design/roadmap.md](design/roadmap.md)
- [design/lifecycle.md](design/lifecycle.md)
- [design/improve_worker.md](design/improve_worker.md)
- [design/plane_kodo_wrapper.md](design/plane_kodo_wrapper.md)
- [design/autonomy/repo_aware_autonomy.md](design/autonomy/repo_aware_autonomy.md)
- [design/execution_budget_and_safety_controls.md](design/execution_budget_and_safety_controls.md)
- [design/autonomy/autonomy_decision_engine.md](design/autonomy/autonomy_decision_engine.md)
- [design/autonomy/autonomy_gaps.md](design/autonomy/autonomy_gaps.md)
- [design/autonomy/autonomy_insight_engine.md](design/autonomy/autonomy_insight_engine.md)
- [design/autonomy/autonomy_proposer_integration.md](design/autonomy/autonomy_proposer_integration.md)
- [design/autonomy/autonomy_repo_observer.md](design/autonomy/autonomy_repo_observer.md)
- [design/autonomy/autonomy_self_tuning_regulator.md](design/autonomy/autonomy_self_tuning_regulator.md)

## Specs

- [specs/proposer-consumes-custodian-sweep.md](specs/proposer-consumes-custodian-sweep.md)
- [specs/reviewer-pr-state-machine.md](specs/reviewer-pr-state-machine.md)
- [specs/watcher-entrypoint-test-coverage.md](specs/watcher-entrypoint-test-coverage.md)

## Demo & Backlog

- [demo.md](demo.md) — End-to-end planning → routing → execution walkthrough.
- [backlog.md](backlog.md) — Backlog of in-flight work (see also `.console/backlog.md`).

## History

One-shot audit reports, completed migrations, and the managed-repo audit
subsystem phase docs (now stable). Retained for context; not authoritative
guidance.

- [history/audits/](history/audits/) — `dod_verification_final`, the rename-refactor
  verifications, integration-invariants verification, `flow_audit`, `ghost_work_audit`.
- [history/migration/](history/migration/) — `controlplane-execution-extraction`.
- [history/managed-repo/](history/managed-repo/) — Managed-repo audit subsystem
  phase docs: dispatch, toolset contract, artifact index, run identity, behavior
  calibration, full-audit governance, fixture harvesting, mini-regression suite,
  slice replay, final verification.
- [history/development-log/](history/development-log/) (under previous `history/`) —
  development log and remediation summaries.
