# Repo-Aware Autonomy Layer

Control Plane’s repo-aware autonomy layer is an internal pipeline that feeds bounded work into the existing proposer lane.

It is intentionally split into four explicit stages:

```text
observe -> analyze -> decide -> propose
```

## Stage Artifacts

```text
tools/report/control_plane/observer/<run_id>/repo_state_snapshot.json
tools/report/control_plane/insights/<run_id>/repo_insights.json
tools/report/control_plane/decision/<run_id>/proposal_candidates.json
tools/report/control_plane/proposer/<run_id>/proposal_results.json
```

Each stage reads the retained output of the previous stage instead of bypassing the artifact boundary.

## Purpose

This layer adds repo-aware initiative without changing the board-facing lifecycle contract.

- `goal`, `test`, `improve`, and `propose` remain the board-facing worker lanes.
- The autonomy layer becomes one more bounded source of proposer input.
- Plane remains the visible source of truth.

## Self-Tuning Loop

The `observe` stage includes an `ExecutionArtifactCollector` that reads the system's own retained kodo_plane execution artifacts. This closes a feedback loop:

```text
[Plane tasks execute] -> [kodo_plane artifacts written]
                                    ↓
                         [ExecutionArtifactCollector]
                                    ↓
                      [ExecutionHealthDeriver: high_no_op_rate,
                        persistent_validation_failures insights]
                                    ↓
                      [ExecutionHealthRule: execution_health_followup
                        candidate → Plane improve task]
```

The loop is automatic. Every `autonomy-cycle` or `observe-repo + generate-insights` run will detect execution quality regressions and propose bounded improve tasks without manual intervention. It does not require a minimum number of consecutive observation runs — the signal is derived from the accumulated artifact history directly.

## Boundaries

- `observe`, `analyze`, and `decide` are read-only against the observed repo.
- Only `propose` writes to Plane.
- No direct git writes or branch creation happen in this layer.
- Zero-candidate and zero-created runs are valid.
- Provenance is retained end to end.
- The execution health collector reads retained system artifacts (not the observed repo) — it is still read-only and produces no side effects.
