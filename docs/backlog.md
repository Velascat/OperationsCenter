# Backlog — Hardening and Trust Phase

Issues tracking the next phase of work before expanding autonomy features.

## Open

### ci — Add GitHub Actions CI workflow
Run ruff and pytest on every push and pull request.
Blocks the green path on lint or test failure.
**Status**: done (`.github/workflows/ci.yml`)

---

### validation — Harden repo/branch/task contract validation
Reject unknown repo keys, missing goal text, and disallowed branches early
with clear operator-facing error messages and Plane comments.
No silent fallback to wrong repo or branch.
**Status**: done (`TaskContractError` in `service.py`)

---

### docs — Add golden-path end-to-end demo
A reproducible walkthrough from local startup to a completed task with
retained artifacts, success/failure signal recognition, and a verification checklist.
**Status**: done (`docs/demo.md`)

---

### config — Polish config templates as first-class product surface
Comments for every major section, multi-repo example, `await_review` example,
execution budget example, PR dry-run example. Templates and README tell the same story.
**Status**: done (`config/control_plane.example.yaml`, `.env.control-plane.example`)

---

### pr-automation — Harden PR automation with dry-run and audit trail
`CONTROL_PLANE_PR_DRY_RUN=1` skips actual PR creation/merge and logs the intended action.
Cleaner structured audit log events around every PR action (`pr_review_pending`, `pr_dry_run`,
`pr_create_failed`, `pr_merged`).
**Status**: done

---

### docs — Clarify primary operator surface in README
Plane + CLI is the primary control model.
Local API/UI (`http://127.0.0.1:8787`) is a helper surface for repo import and live board view.
**Status**: done (README updated)

---

## Phase C (complete)

### autonomy — `analyze-artifacts` threshold tuning tool
Reads all retained decision + proposer artifacts and computes per-family stats
(emitted, suppressed, created, guardrail-skipped, suppression reasons).
Prints recommendations when suppression rate ≥ 90% or emitted tasks never created.
**Status**: done (`src/control_plane/entrypoints/analyze/main.py`, `scripts/control-plane.sh analyze-artifacts`)

---

### autonomy — `autonomy-cycle` dry-run-first wrapper
Chains `observe → insights → decide → propose` in one command.
Dry-run by default (shows what would be proposed); `--execute` creates real Plane tasks.
`--all-families` enables hotspot_concentration and todo_accumulation families.
**Status**: done (`src/control_plane/entrypoints/autonomy_cycle/main.py`, `scripts/control-plane.sh autonomy-cycle`)

---

### validation — Contract validation for proposer candidates
Proposer `candidate_mapper` raises `ValueError` with clear message instead of silently
falling back to the first known repo when a candidate has no matching repo key.
**Status**: done (`proposer/candidate_mapper.py`)

---

### config — Non-Python repo bootstrap support
`bootstrap_commands` list in `RepoSettings` replaces Python venv setup when set,
allowing non-Python repos to declare their own install steps.
**Status**: done (`config/settings.py`, `adapters/workspace/bootstrap.py`)

---

## Next

### config — Per-repo execution budget overrides
Allow repos to declare their own hourly/daily caps rather than sharing the global budget.

### ci — Add type checking to CI
Integrate `ty check` (or mypy) into the CI workflow once the type coverage baseline is clean.
