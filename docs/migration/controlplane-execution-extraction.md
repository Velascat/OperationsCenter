# ControlPlane Execution Extraction

- Default worker entrypoint now builds a `TaskProposal`, routes it, and emits a proposal/decision bundle.
- Default reviewer entrypoint is retired and no longer runs the legacy execution loop.
- ARCHIVAL NOTE: the interim `control_plane.legacy_execution` quarantine path has been deleted.
- ARCHIVAL NOTE: canonical execution now runs only through `control_plane.execution.coordinator`.
- Spec-director LLM calls no longer route through a SwitchBoard proxy surface.
