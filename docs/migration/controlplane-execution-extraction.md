# ControlPlane Execution Extraction

- Default worker entrypoint now builds a `TaskProposal`, routes it, and emits a proposal/decision bundle.
- Default reviewer entrypoint is retired and no longer runs the legacy execution loop.
- `ExecutionService` is no longer part of the exported application surface.
- Legacy execution types were renamed to `LegacyExecutionRequest` and `LegacyExecutionResult`.
- Spec-director LLM calls no longer route through a SwitchBoard proxy surface.
