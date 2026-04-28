# Audit Triage Plan

Proposed treatment for every finding across the three audit catalogs
(`ghost_work_audit`, `flow_audit`, `code_health_audit`). Each item lists
intent, cost (**S** ≤ 30 min, **M** ≤ 2 h, **L** > 2 h), and dependencies.
We walk through and pick.

---

## Group A — Delete (lowest risk, highest signal)

Keep moving by clearing weight first. Nothing here is structurally needed.

| ID | Action | Cost | Notes |
|----|--------|------|-------|
| A1 | **Delete `backends/openclaw/`** (1,100 LOC) | S | Raises `NotImplementedError`; factory only wires when a runner is passed; never passed in production. If you want OpenClaw later, the kodo backend is the model and re-scaffolding from kodo is faster than reading 1,100 LOC of stubs. |
| A2 | **Delete `backends/archon/`** (957 LOC) | S | Same shape as A1 — full scaffolding, no live use. |
| A3 | **Delete `openclaw_shell/`** (801 LOC) | S | Only consumer is the openclaw backend (A1). Falls with A1. |
| A4 | **Delete dead settings fields** | M | 42 fields C7 found. Need to triage each — *kill* the ones that were never wired (most), *implement* the few that are real knobs (see Group C). |

**Proposed cut for A4**: I show you the 42 fields, you mark each `kill / wire / keep-for-future`, I apply the cut. ~10 min of your time.

---

## Group B — Wire up orphans (small surface)

| ID | Action | Cost | Notes |
|----|--------|------|-------|
| B1 | Add `autonomy_tiers` to `pyproject.toml` console_scripts | S | Real implementation exists. Currently no way to invoke it. Just add an entry. |
| B2 | `ci_monitor` — wire OR delete | S | Need to glance at it first. If it's a useful one-shot CLI, wire it; if it's dead, delete it. |
| B3 | Wire `code_health_audit` into a recurring CI job | M | Run on PR / nightly, fail if `total_findings` regresses. Pure mechanical — Github Actions yaml. |

---

## Group C — Implement the knob (settings fields that should be alive)

These are the *kept* fields from A4 — config values that were designed-in
and should be honored. Two of them shape behavior visibly:

| ID | Action | Cost | Notes |
|----|--------|------|-------|
| C-K1 | Honor `RepoSettings.auto_merge_on_ci_green` in `pr_review_watcher` | M | Currently the field is dead; reviewer never auto-merges based on CI. Plumb it to gate Phase 1 LGTM → merge on `ci_status=success`. |
| C-K2 | Honor `git.author_name` / `git.author_email` in `WorkspaceManager._bot_identity` | S | We hardcoded `"Operations Center"` / `"operations-center@local"`. Read from settings instead so commits attribute correctly. |
| C-K3 | Honor `RepoSettings.bootstrap_enabled` in execute pipeline | M | Currently bootstrap is always attempted. Field exists, just unused. |

(Other fields TBD during the A4 triage.)

---

## Group D — Flow gaps with concrete fixes (medium effort)

| ID | Action | Cost | Notes |
|----|--------|------|-------|
| D1 | **F2** transient kodo retry | M | Detect transient categories (network timeout, 502, 429 not yet quota'd); retry once with fresh workspace before Blocked. ~50 LOC in `WorkspaceManager` + a settings knob `transient_retry_count: int = 1`. |
| D2 | **F4** quota-aware throttling | S | Pre-claim check: if today's run count for repo ≥ `max_daily_executions`, skip claim and log. Field already exists; just wire the consumer. |
| D3 | **F8** Ready-queue back-pressure | S | Lower default `propose_skip_when_ready_count` from 8 → 5 (current default already there, just tighten). Surface in flow_audit when exceeded. |
| D4 | **F12** stall alerting | M | Watchdog already detects heartbeat staleness; needs a notification path. Simplest: status pane shows a red banner if any role's heartbeat is > 10 min old. Bigger: write a structured alert to `state/alerts/` that an external integration can read. |

---

## Group E — Bigger features (large effort, real value)

| ID | Action | Cost | Notes |
|----|--------|------|-------|
| E1 | **F5** task dependencies | L | `depends-on: <task_id>` label honored by `_claim_next` (skip when dep not Done). Needed for ordering; foundational for E5. |
| E2 | **F6** campaign progress panel | L | Status-pane card sourced from `state/campaigns/active.json` + Plane child counts. Burndown bar per campaign. |
| E3 | **F9** per-task cost accounting | L | Wrap kodo invocation in a token-counting recorder; aggregate per family / per repo. Foundation for budget enforcement. |
| E4 | **F10** intent verification (different model for review) | L | Phase 1 self-review currently uses the same model that wrote the code. Make reviewer use one tier up (Sonnet → Opus, etc.). Real friction without human bottleneck. |
| E5 | **F15** reviewer trust ramp-up | L | Track LGTM-then-clean ratio per repo in `state/reviewer_trust/`. Gate `auto_merge_on_ci_green` behind it. Depends on E3 partly. |

---

## Recommended sequence

1. **A1, A2, A3** — delete the three dead backends (~2,800 LOC out). Zero risk.
2. **A4 triage** — walk the 42 dead settings fields together; mark kill/wire/keep. I apply the cut.
3. **B1, B2** — quick wire-ups for the orphaned entrypoints.
4. **C-K2** — `author_name`/`author_email` (smallest of the knobs).
5. **D2, D3** — quota throttling + back-pressure (small wins).
6. **D1** — transient retry (medium).
7. **D4** — alerting (medium).
8. **C-K1** — auto-merge-on-CI (medium).
9. **C-K3** — bootstrap_enabled (medium).
10. **B3** — code_health audit in CI (medium).
11. **E1, E2, E3** — bigger features, schedule individually.

Steps 1–5 are ~half a day's work and cut the catalog by ~70%. Steps
6–10 are another half day. E1–E5 are project-sized.

---

## What to do right now

Pick a row. I'll implement it, run the audits to confirm, commit, then
we move to the next. If you want to batch (e.g. "do all of group A,
then check in"), say so and I'll bundle.
