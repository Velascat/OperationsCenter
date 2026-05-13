# Development History

Historical implementation records, completed audit reports, finished migrations,
phase-based development notes, and remediation summaries. Retained for context
and inbound links — these do not reflect current architecture guidance.

Current architecture documentation lives in `docs/architecture/` (OC-specific)
and [PlatformDeployment/docs/architecture/](https://github.com/ProtocolWarden/PlatformDeployment/tree/main/docs/architecture)
(canonical platform).

## Layout

- **[audits/](audits/)** — One-shot audit reports: `dod_verification_final`,
  the rename-refactor verifications, integration-invariants verification,
  `flow_audit`, `ghost_work_audit`.
- **[migration/](migration/)** — Completed migration plans:
  `controlplane-execution-extraction`.
- **[managed-repo/](managed-repo/)** — Managed-repo audit subsystem phase
  docs (now stable): dispatch, toolset contract, artifact index, run identity,
  behavior calibration, full-audit governance, fixture harvesting,
  mini-regression suite, slice replay, final verification.
- **[development-log/](development-log/)** — Per-feature design and decomposition
  logs from the autonomous-spec-driven-chain era.
- **runtime-truth-remediation-summary.md** — Summary of the runtime-truth
  remediation pass.
