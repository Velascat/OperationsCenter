# ControlPlane Execution Extraction

- Default worker entrypoint now builds a `TaskProposal`, routes it, and emits a proposal/decision bundle.
- Default reviewer entrypoint is retired and no longer runs the legacy execution loop.
- `ExecutionService` is no longer part of the exported application surface and now lives under `control_plane.legacy_execution`.
- Legacy execution types now live under `control_plane.legacy_execution.models`.
- Spec-director LLM calls no longer route through a SwitchBoard proxy surface.
