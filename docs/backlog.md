# Backlog — Post-Hardening: Tuning, Trust, and Public Packaging

The hardening phase is complete. The system is running with two active repos (ControlPlane and code_youtube_shorts), a functional six-lane watcher (including the new spec-director), repo-aware autonomy loop, and PR review automation. This phase focuses on tuning the autonomy loop, building operator trust, and polishing the public-facing surface.

## Active

### tuning — Run analyze-artifacts loop and document threshold adjustments
Run `analyze-artifacts` weekly, identify suppression patterns, tune per-family thresholds, document changes.
Tracked: [#17](https://github.com/Velascat/ControlPlane/issues/17)
**Status**: in progress (`docs/operator/tuning.md` created; first tuning run pending)

---

### tuning — Candidate-family heuristics review
Review emit/suppress rates for observation_coverage, test_visibility, dependency_drift. Define promotion criteria for hotspot_concentration and todo_accumulation.
Tracked: [#18](https://github.com/Velascat/ControlPlane/issues/18)
**Status**: open

---

### trust — Validate PR review loop end-to-end
Run the two-phase review loop against a real or controlled test PR. Verify audit trail, guardrail checklist, and escalation path.
Tracked: [#19](https://github.com/Velascat/ControlPlane/issues/19)
**Status**: open (`docs/operator/pr_review.md` created; live validation pending)

---

### docs — Promote golden-path demo as ongoing validation ritual
README and demo.md updated to position the demo as a post-change ritual. Ensure demo stays runnable as config and thresholds change.
Tracked: [#20](https://github.com/Velascat/ControlPlane/issues/20)
**Status**: done (demo.md updated with Autonomy-Cycle Ritual section; README updated)

---

### docs — Polish public docs packaging
Operator docs suite: tuning.md, pr_review.md, runtime.md (dry-run-first), README cross-links, backlog update.
Tracked: [#21](https://github.com/Velascat/ControlPlane/issues/21)
**Status**: done (all operator docs created/updated)

---

## Completed (Hardening Phase)

### ci — Add GitHub Actions CI workflow
**Status**: done (`.github/workflows/ci.yml`)

### validation — Harden repo/branch/task contract validation
**Status**: done (`TaskContractError` in `service.py`)

### docs — Add golden-path end-to-end demo
**Status**: done (`docs/demo.md`)

### config — Polish config templates as first-class product surface
**Status**: done (`config/control_plane.example.yaml`, `.env.control-plane.example`)

### pr-automation — Harden PR automation with dry-run and audit trail
**Status**: done

### docs — Clarify primary operator surface in README
**Status**: done

### autonomy — `analyze-artifacts` threshold tuning tool
**Status**: done (`src/control_plane/entrypoints/analyze/main.py`)

### autonomy — `autonomy-cycle` dry-run-first wrapper
**Status**: done (`src/control_plane/entrypoints/autonomy_cycle/main.py`)

### validation — Contract validation for proposer candidates
**Status**: done (`proposer/candidate_mapper.py`)

### config — Non-Python repo bootstrap support
**Status**: done (`config/settings.py`, `adapters/workspace/bootstrap.py`)

### ci — Type checking with `ty`
**Status**: done (`.github/workflows/ci.yml`, `pyproject.toml`)

### autonomy — Per-repo board idle check
**Status**: done (proposer filters by `repo:` label before evaluating idle state)

### autonomy — Proposal interleaving across repos
**Status**: done (round-robin by repo_key ensures fair multi-repo distribution)

### reliability — Rate-limited task self-healing
**Status**: done (rate-limited tasks reset to Ready for AI automatically; budget not charged)

### tests — Injectable UsageStore for isolated test runs
**Status**: done (ProposerGuardrailAdapter and DecisionEngineService accept `usage_store` param)

---

## Completed (Regulation Loop Phase)

### autonomy — Bounded self-tuning regulation loop (`tune-autonomy`)
**Status**: done

`TuningRegulatorService` aggregates per-family metrics from retained decision and proposer artifacts and applies explicit recommendation rules (over-suppressed → loosen; noisy → tighten; healthy → keep). Recommendation-only by default; auto-apply mode (opt-in via `--apply` + `CONTROL_PLANE_TUNING_AUTO_APPLY_ENABLED=1`) writes conservative bounded changes to `config/autonomy_tuning.json` with full cooldown, quota, oscillation, and allowlist guardrails. `DecisionEngineService` reads tuning overrides at startup. Full audit trail retained in `tools/report/control_plane/tuning/`. 47 tests.

---

## Completed (Session 12 — Spec-Director)

### autonomy — Fully autonomous spec-driven campaign chain (`watch --role spec`)
**Status**: done

New sixth watcher role `spec` that closes the direction gap in the reactive propose loop. `TriggerDetector` detects when to start a campaign (drop-file > Plane label > queue drain). `BrainstormService` calls the Anthropic API directly to produce a spec doc written to `docs/specs/`. `CampaignBuilder` converts the spec into a bounded set of Plane tasks across implement/test/improve phases. `SpecComplianceService` reviews each PR diff against the spec (structured JSON verdict) upstream of kodo self-review. `RecoveryService` handles stall detection, spec revision, and orderly self-cancel. `Suppressor` blocks conflicting heuristic proposals during an active campaign. New task kinds `test_campaign` and `improve_campaign` route to `kodo --test` / `kodo --improve` via `ROLE_TASK_KINDS` in `worker/main.py`.

---

## Completed (Post-Hardening)

### autonomy — Execution health self-tuning loop
**Status**: done

`ExecutionArtifactCollector` reads retained kodo_plane artifacts on every observer run and computes per-repo execution quality metrics (`total_runs`, `no_op_count`, `executed_count`, `validation_failed_count`). `ExecutionHealthDeriver` derives `high_no_op_rate` and `persistent_validation_failures` insights. `ExecutionHealthRule` converts them into `execution_health_followup` candidates. The family is in `_DEFAULT_ALLOWED_FAMILIES` so it fires automatically. No manual trigger needed.

---

## Completed (Phase 4 — Validation Profiles + Evidence Bundles)

### autonomy — Validation profiles per candidate family
**Status**: done

Five profile constants (`ruff_clean`, `ty_clean`, `tests_pass`, `ci_green`, `manual_review`) defined in `validation_profiles.py`. All 12 families mapped via `profile_for_family()`. `CandidateBuilder` auto-assigns from family; rules may override explicitly. `validation_profile`, `requires_human_approval`, and `evidence_schema_version` appear in every created task's `## Provenance` block. `emitted_candidates` list with `{family, validation_profile, confidence}` per emitted candidate in `cycle_<ts>.json` report.

### autonomy — EvidenceBundle structured machine-readable evidence
**Status**: done

`EvidenceBundle` Pydantic model (`kind`, `count`, `distinct_file_count`, `delta`, `trend`, `top_codes`, `source`, `schema_version`) synthesized by `CandidateBuilder._synthesize_evidence_bundle()` for `lint_fix` and `type_fix`; `None` for other families. `distinct_file_count` is a true count from the full violation/error output (not bounded by the top-N sample window).

---

## Next

Items here are promoted to the board automatically by the `backlog_promotion` family (when enabled).
`type: arch` and `type: redesign` items are **never** auto-promoted — they require deliberate operator action.

---

### autonomy — Promote hotspot_concentration and todo_accumulation families
**Type**: maintenance
After observing healthy emit/create rates for the four default families, promote hotspot and todo families to `_DEFAULT_ALLOWED_FAMILIES`. Requires documented promotion criteria and at least one clean dry-run showing useful candidates.

### autonomy — Family-specific tuning refinement based on real regulator behavior
**Type**: maintenance
After the first few `tune-autonomy` runs accumulate retained artifacts, review recommendations against actual board outcomes and adjust the recommendation rule thresholds (OVER_SUPPRESSED_RATE, NOISY_CREATE_RATE_CEILING, HEALTHY_CREATE_RATE_FLOOR) if the defaults are too aggressive or too conservative for the observed repos.

### autonomy — Revisit dry-run-first posture for trusted repos
**Type**: feature
Define "trusted" in terms of measurable execution health and tuning stability (low no-op rate, clean validation history, healthy create/emit ratio stable across N regulator runs). Add a `trusted_repos` or `auto_execute_families` config key so specific repos skip the dry-run gate automatically.

### config — Per-repo execution budget overrides
**Type**: feature
Allow repos to declare their own hourly/daily caps rather than sharing the global budget. Useful when code_youtube_shorts and ControlPlane have different execution intensity.

### ci — Enforce `analyze-artifacts` output as a CI artifact
**Type**: maintenance
Run `analyze-artifacts` in CI on the retained artifacts directory and upload the output as a build artifact. Makes the tuning loop auditable per-commit.
