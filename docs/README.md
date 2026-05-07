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

- [architecture/audit_architecture.md](architecture/audit_architecture.md) —
  Audit-dispatch architecture inside OC.
- [architecture/audit_triage_plan.md](architecture/audit_triage_plan.md)
- [architecture/anti_collapse_invariant.md](architecture/anti_collapse_invariant.md)
- [architecture/backend_control_audit.md](architecture/backend_control_audit.md)
- [architecture/ci_integration_guide.md](architecture/ci_integration_guide.md)
- [architecture/code_health_audit.md](architecture/code_health_audit.md)
- [architecture/contract-map.md](architecture/contract-map.md)
- [architecture/execution-handoff-cutover.md](architecture/execution-handoff-cutover.md)
- [architecture/execution_target.md](architecture/execution_target.md)
- [architecture/flow_audit.md](architecture/flow_audit.md)
- [architecture/ghost_work_audit.md](architecture/ghost_work_audit.md)
- [architecture/lifecycle_labels.md](architecture/lifecycle_labels.md)
- [architecture/phantom_helper_waves.md](architecture/phantom_helper_waves.md)
- [architecture/policy-pre-execution-gate.md](architecture/policy-pre-execution-gate.md)
- [architecture/recovery_loop_design.md](architecture/recovery_loop_design.md)
- [architecture/routing-contract-fidelity.md](architecture/routing-contract-fidelity.md)
- [architecture/routing-tuning.md](architecture/routing-tuning.md) ·
  [examples](architecture/routing-tuning-examples.md)
- [architecture/upstream-patch-evaluation.md](architecture/upstream-patch-evaluation.md) ·
  [examples](architecture/upstream-patch-evaluation-examples.md)
- [architecture/adr/](architecture/adr/) — OC architecture decision records.

### Managed-repo audit subsystem

- [architecture/managed_repo_audit_dispatch.md](architecture/managed_repo_audit_dispatch.md)
- [architecture/managed_repo_audit_toolset_contract.md](architecture/managed_repo_audit_toolset_contract.md)
- [architecture/managed_repo_artifact_index.md](architecture/managed_repo_artifact_index.md)
- [architecture/managed_repo_run_identity.md](architecture/managed_repo_run_identity.md)
- [architecture/managed_repo_behavior_calibration.md](architecture/managed_repo_behavior_calibration.md)
- [architecture/managed_repo_full_audit_governance.md](architecture/managed_repo_full_audit_governance.md)
- [architecture/managed_repo_fixture_harvesting.md](architecture/managed_repo_fixture_harvesting.md)
- [architecture/managed_repo_mini_regression_suite.md](architecture/managed_repo_mini_regression_suite.md)
- [architecture/managed_repo_slice_replay.md](architecture/managed_repo_slice_replay.md)
- [architecture/managed_repo_audit_system_final_verification.md](architecture/managed_repo_audit_system_final_verification.md)

### VideoFoundry-specific

- [architecture/videofoundry_managed_repo_contract.md](architecture/videofoundry_managed_repo_contract.md)
- [architecture/videofoundry_audit_artifact_contract.md](architecture/videofoundry_audit_artifact_contract.md)
- [architecture/videofoundry_audit_ground_truth.md](architecture/videofoundry_audit_ground_truth.md)

## Design

- [design/roadmap.md](design/roadmap.md)
- [design/lifecycle.md](design/lifecycle.md)
- [design/improve_worker.md](design/improve_worker.md)
- [design/plane_kodo_wrapper.md](design/plane_kodo_wrapper.md)
- [design/repo_aware_autonomy.md](design/repo_aware_autonomy.md)
- [design/execution_budget_and_safety_controls.md](design/execution_budget_and_safety_controls.md)
- [design/autonomy_decision_engine.md](design/autonomy_decision_engine.md)
- [design/autonomy_gaps.md](design/autonomy_gaps.md)
- [design/autonomy_insight_engine.md](design/autonomy_insight_engine.md)
- [design/autonomy_proposer_integration.md](design/autonomy_proposer_integration.md)
- [design/autonomy_repo_observer.md](design/autonomy_repo_observer.md)
- [design/autonomy_self_tuning_regulator.md](design/autonomy_self_tuning_regulator.md)

## Specs

- [specs/proposer-consumes-custodian-sweep.md](specs/proposer-consumes-custodian-sweep.md)
- [specs/reviewer-pr-state-machine.md](specs/reviewer-pr-state-machine.md)
- [specs/watcher-entrypoint-test-coverage.md](specs/watcher-entrypoint-test-coverage.md)

## Audits

- [audits/dod_verification_final.md](audits/dod_verification_final.md)
- [audits/integration_invariants_verification.md](audits/integration_invariants_verification.md)
- [audits/final_rename_refactor_verification.md](audits/final_rename_refactor_verification.md)
- [audits/final_rename_refactor_verification_3.md](audits/final_rename_refactor_verification_3.md)

## Other

- [demo.md](demo.md) — End-to-end planning → routing → execution walkthrough.
- [backlog.md](backlog.md) — Backlog of in-flight work (see also `.console/backlog.md`).
- [history/](history/) — Development log + historical remediation summaries.
- [migration/controlplane-execution-extraction.md](migration/controlplane-execution-extraction.md)
