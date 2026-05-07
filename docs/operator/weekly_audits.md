---
status: active
---

# Weekly audit runbook

These three audits are operator-invoked CLIs. They are read-only — none
of them mutate Plane, git, or any state. Run them weekly (or on demand)
to keep the autonomy loop honest.

## The three audits

| Command | What it covers | Source of truth |
|---|---|---|
| `custodian-audit --repo .` | Codebase audits — C1–C8 generic + OC1–OC9 + AI1–AI4 + DC1–DC5 | source tree only |
| `operations-center-ghost-audit --config <conf>` | Runtime / log-derived audits (ghost work) | logs + Plane |
| `operations-center-flow-audit --config <conf>` | Plane state-flow audits (stale tasks, dupe proposals, …) | Plane + state files |

## Suggested cadence

**Weekly** (Mondays, before triage):

```bash
# 1. Code-health across every managed repo with Custodian
operations-center-custodian-sweep \
    --config config/operations_center.local.yaml \
    --emit

# 2. Runtime audits (this OC instance only — needs Plane)
operations-center-ghost-audit \
    --config config/operations_center.local.yaml \
    --since 7d > /tmp/ghost-audit.json
operations-center-flow-audit \
    --config config/operations_center.local.yaml > /tmp/flow-audit.json
```

After running, triage in this order:

1. **Custodian sweep tasks in Plane** — one per managed repo, labeled
   `custodian-sweep`. Skim the deltas; pick the regressions worth
   fixing. Each becomes a goal task (until the proposer-consumes-sweep
   spec lands; see `docs/specs/proposer-consumes-custodian-sweep.md`).
2. **Ghost audit JSON** — anything with `count > 0` in `Open` status
   is a real gap. Fix or formally exempt.
3. **Flow audit JSON** — same shape. Stale Running tasks, dupe
   proposals, runaway retries surface here.

## Where the rules are documented

| Audit | Catalog |
|---|---|
| Custodian generic (C1–C8) | Custodian repo `audit_kit/` |
| OC code-health (OC1–OC9) | `_custodian/detectors.py` + `docs/architecture/code_health_audit.md` |
| Architecture invariants (AI1–AI4) | `_custodian/architecture.py` + `tools/audit/architecture_invariants/` |
| Doc conventions (DC1–DC5) | `_custodian/doc_conventions.py` |
| Ghost work (G1–GN) | `docs/history/audits/ghost_work_audit.md` |
| Flow gaps (F1–FN) | `docs/history/audits/flow_audit.md` |

## How the cadence is enforced

OC has no cron daemon. Instead the propose cycle is the scheduler:
each cycle, `scheduled_tasks` entries in
`config/operations_center.local.yaml` are checked, and any whose
`every` interval has elapsed (gated by optional `at:` and `on_days:`)
get seeded as Ready-for-AI Plane tasks.

This file ships with one entry: a Monday 13:00 UTC weekly task titled
`"Weekly audit triage"` whose goal text tells the operator (or
eventually the executor) to run the three CLIs above and triage their
output. The `await_review: true` setting means the seeded task waits
for a human label before any code change happens — the schedule fires
the *reminder*, not the *fix*.

To pause the cadence, comment out or remove the entry from
`scheduled_tasks:` in the local config. To shift the day/time,
change `on_days:` and `at:`. The
`state/scheduled_tasks_last_run.json` file remembers when each
schedule last fired so renames restart the clock and missed cycles
don't fire-storm on resume.

## Related

- `docs/architecture/audit_architecture.md` — the Custodian / OC split
- `docs/specs/proposer-consumes-custodian-sweep.md` — future automation
  that turns sweep findings directly into goal tasks
