# Audit Triage Plan

Live state across all three audits. Updated 2026-04-28 after the
scheduled-tasks feature shipped.

```
ghost_audit       12 patterns   total: 5    (all historical, pre-fix)
flow_audit         5 detectors  total: 0    open gaps
code_health_audit  8 patterns   total: 190  (mostly C7 settings + C8 phantoms)
```

---

## Group A — Delete

| ID | Action | Cost | Status |
|----|--------|------|--------|
| A1 | Delete `backends/openclaw/` (1,100 LOC stub) | S | **Held — keep for future use** |
| A2 | Delete `backends/archon/` (957 LOC stub) | S | **Held — keep for future use** |
| A3 | Delete `openclaw_shell/` (801 LOC) | S | **Held — falls with A1** |
| A4 | Triage 35 dead settings fields | M | **In progress** — see C7 catalog below |

---

## Group B — Wire orphans

| ID | Action | Cost | Status |
|----|--------|------|--------|
| B1 | Add `autonomy_tiers` to `pyproject.toml` console_scripts | S | Pending |
| B2 | `ci_monitor` — wire OR delete | S | Pending |
| B3 | Wire `code_health_audit` into a CI job | M | Pending |

---

## Group C — Implement the knob (selected from C7)

Settings fields that should be alive. Picked from the 35 dead C7 fields
where the implementation cost is small AND the value is concrete.

### Wire-now batch (this Option 1 bundle)

| ID | Field | Class | Wire to |
|----|-------|-------|---------|
| C-K2 | `author_name` + `author_email` | `GitSettings` | `WorkspaceManager._bot_identity` reads from settings |
| D2-wire | `max_daily_executions` | `RepoSettings` | board_worker `_claim_next` quota check |
| D3-wire | `propose_skip_when_ready_count` | `Settings` | propose lane skips when Ready ≥ N |
| C-K-prop | `propose_enabled` | `RepoSettings` | propose lane skips repos with this False |

### Wire-next bundle (after first batch ships)

| ID | Field(s) | Notes |
|----|----------|-------|
| C-K1 | `auto_merge_on_ci_green` | reviewer Phase 1 auto-merge gate |
| C-K3 | `bootstrap_enabled`, `python_binary`, `venv_dir`, `install_dev_command`, `bootstrap_commands` | execute-pipeline bootstrap chain |
| C-K4 | `validation_timeout_seconds` | per-repo execution timeout |
| C-K5 | `skip_baseline_validation` | skip pre-flight validation |
| C-K6 | `require_explicit_approval` | per-repo opt-out of trusted-source bypass |
| C-K7 | `ci_ignored_checks` | reviewer ignores these CI checks |
| C-K8 | `impact_report_paths` | cross-repo impact warning |
| C-K9 | `open_pr_default` + `push_on_validation_failure` | git workflow knobs |
| C-K10 | `stale_pr_days` + `stale_autonomy_backlog_days` | TTL thresholds |

### Defer (depend on bigger features E1–E5)

| ID | Field(s) | Depends on |
|----|----------|-----------|
| C-D1 | `auto_merge_success_rate_threshold` | E5 reviewer trust ramp-up |
| C-D2 | `parallel_slots`, `max_concurrent_kodo`, `min_kodo_available_mb` | concurrency-control feature |
| C-D3 | `focus_areas` | propose tuning |
| C-D4 | All `SpecDirectorSettings` fields | spec_director enhancements |
| C-D5 | `EscalationSettings.block_threshold`, `credential_expiry_warn_days` | F12 alerting |

### Keep (future-proofing)

`GitSettings.provider`, `sign_commits`, `signing_key` — placeholders for non-default git workflows.

---

## Group D — Flow gaps with concrete fixes

| ID | Action | Cost | Status |
|----|--------|------|--------|
| D1 | **F2** transient kodo retry | M | Pending |
| D2 | **F4** quota-aware throttling consumer | S | Pending — wires `max_daily_executions` |
| D3 | **F8** Ready-queue back-pressure | S | Pending — wires `propose_skip_when_ready_count` |
| D4 | **F12** stall alerting | M | Pending |

---

## Group E — Bigger features (large effort, real value)

| ID | Action | Cost |
|----|--------|------|
| E1 | **F5** task dependencies (`depends-on:` label) | L |
| E2 | **F6** campaign progress panel | L |
| E3 | **F9** per-task cost accounting | L |
| E4 | **F10** intent verification (different model for review) | L |
| E5 | **F15** reviewer trust ramp-up | L |

---

## Group F — Documentation reconciliation (NEW — from C8)

The phantom-symbol detector flagged **151 references in design docs to
functions that don't exist**. Almost all in `docs/design/autonomy_gaps.md`,
which turns out to be aspirational rather than status. Per-section triage:

| ID | Action | Cost | Status |
|----|--------|------|--------|
| F-A | Walk autonomy_gaps.md and mark each section: **shipped** / **deferred [reviewed YYYY-MM-DD]** / **delete** | M | Pending — would dramatically cut C8 noise |
| F-B | For each "deferred" section, ensure no README claim says it works | S | Bundled with F-A |
| F-C | Implement any sections marked "ship" via the normal triage | varies | Per-section |

Recommended cadence: walk autonomy_gaps.md once, mark every section, then
treat C8 count as a regression detector going forward (any new doc that
adds phantom symbols shows up).

---

## Recommended sequence (updated)

1. **A4 wire-now batch** — 4 fields (`author_name`/`author_email`/`propose_skip_when_ready_count`/`max_daily_executions`/`propose_enabled`) wired in one commit
2. **B1, B2** — orphaned entrypoints
3. **D2, D3** — flow gaps that just need consumers (already covered by A4 batch)
4. **D1, D4** — transient retry + alerting
5. **C-K1, C-K3, C-K4, C-K9** — auto-merge + bootstrap chain + validation timeout + git workflow knobs
6. **F-A** — walk autonomy_gaps.md, mark sections
7. **B3** — code_health audit in CI
8. **E1–E5** — bigger features, scheduled individually

Steps 1–4 = ~half a day. 5–7 = another half day. E1–E5 are
project-sized.

---

## What's already shipped (since this plan was written)

- ✅ Lifecycle vocabulary: `expanded`, `superseded`, `escalated`, `archived`
- ✅ Flow-audit fixes: F1 (stale Running recovery), F11 (retry counter), F13 (state cleanup)
- ✅ Maintenance tools: `recover_stale`, `cleanup_state` CLIs, wired in `pyproject.toml`
- ✅ Trinity of audit CLIs: `ghost_audit`, `flow_audit`, `code_health_audit`
- ✅ Scheduled task seeder (no cron, finished)
- ✅ Phantom-symbol detector (C8) — found the gap that was hiding scheduled_tasks

---

## What to do next

Pick a row. I'll implement it, run the audits to confirm, commit, then
we move to the next. If you want to batch (e.g. "do the wire-now batch
now"), say so and I'll bundle.
