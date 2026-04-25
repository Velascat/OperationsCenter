## Runtime Fidelity Repair

This patch closes the remaining evidence-quality gaps in the supported runtime without changing architectural boundaries.

- `KodoBackendAdapter` now exposes `execute_and_capture()` and `build_backend_detail_refs()`, matching the existing Archon and OpenClaw pattern. Kodo retains raw stdout/stderr and a structured run-capture artifact by reference under `.operations_center/backend_details/<run_id>/`, while the canonical `ExecutionResult` remains backend-agnostic.
- `ExecutionCoordinator` now persists the tuning-relevant metadata that the comparison layer actually consumes. Every retained `ExecutionRecord` records proposal-truth `task_type` and `risk_level`, and capture-capable adapters contribute truthful `duration_ms` from runtime capture when available.
- The tuning comparison tests were repaired by aligning the fixtures with the observability model’s evidence semantics. Successful test records that are meant to represent authoritative changed-file evidence now set `changed_files_source="backend_manifest"` explicitly instead of relying on unspecified provenance.

Remaining adapter differences are limited to backend-native detail shape. Archon retains workflow events, OpenClaw retains event streams and changed-file provenance, and Kodo retains stdout/stderr plus structured capture because those are the raw details its supported path can truthfully expose today.
