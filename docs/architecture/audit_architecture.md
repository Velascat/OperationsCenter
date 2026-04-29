---
status: active
---

# Audit Architecture

This doc describes how Custodian and OperationsCenter divide
responsibility for audits, runtime checks, and maintenance.

## The split, in one diagram

```
┌────────────────────────────────────────────────────────────────┐
│  Custodian  (separate repo, pip dep, v0.1 locked)              │
│  ──────────                                                    │
│  audit_kit/      Detector contract, AuditContext, run_audit()  │
│                  Generic C1-C8 detectors (TODO / print / etc.) │
│  plugins/        loader, LogScanner / StateScanner protocols   │
│  maintenance_kit Trivial list filters                          │
│  cli/            custodian-audit, custodian-doctor             │
└────────────────────────────────────────────────────────────────┘
              ▲ called by
              │
┌────────────────────────────────────────────────────────────────┐
│  OperationsCenter (this repo)                                  │
│  ────────────────                                              │
│                                                                │
│  .custodian.yaml         Declares repo_key + paths +           │
│                          which plugins / detectors to load     │
│                                                                │
│  _custodian/             Consumer plugin layer (OC's OWN code) │
│   ├── detectors.py       OC1-OC9   (code-health overlays)      │
│   ├── architecture.py    AI1-AI4   (wraps tools/audit/...)     │
│   ├── doc_conventions.py DC1-DC5   (doc hygiene)               │
│   ├── log_scanner.py     OCLogScanner (LogScanner protocol)    │
│   └── state_scanner.py   OCStateScanner (StateScanner protocol)│
│                                                                │
│  tools/audit/            ARCHITECTURE rule logic — pre-existing│
│   architecture_invariants/   tooling that AI1-AI4 thinly wraps │
│                                                                │
│  src/operations_center/  OC's runtime — completely untouched   │
│   entrypoints/                                                 │
│    ├── ghost_audit/      runtime audit (Plane + logs)          │
│    │                     not via Custodian — needs Plane API   │
│    │                     uses OCLogScanner for line parsing    │
│    ├── flow_audit/       runtime audit (state files + Plane)   │
│    │                     not via Custodian — needs Plane API   │
│    │                     uses OCStateScanner for subdirs       │
│    ├── maintenance/      operator CLIs (recover_stale, etc.)   │
│    │                     not audits — they MUTATE Plane state  │
│    └── ... (board_worker, reviewer, etc. — runtime watchers)   │
└────────────────────────────────────────────────────────────────┘
```

## Who calls what

**`custodian-audit`** (entry point, lives in Custodian):

1. Reads `.custodian.yaml`
2. Always runs Custodian's generic **C1–C8**
3. Loads plugins (OCLogScanner, OCStateScanner) → puts them in
   `AuditContext.plugin_modules`
4. Loads detector contributors:
   - `_custodian.detectors:build_oc_detectors`            → OC1–OC9
   - `_custodian.architecture:build_oc_architecture_detectors` → AI1–AI4
   - `_custodian.doc_conventions:build_oc_doc_convention_detectors` → DC1–DC5
5. Runs all 26 detectors against `AuditContext`, emits one JSON
   `{schema_version, repo_key, total_findings, patterns: {...}}`

**OC's `ghost_audit` / `flow_audit`** are *not* run by Custodian. They
need Plane API access and time-window filtering that don't fit
Custodian's pure-code-of-the-tree audit shape. They live as standalone
CLIs but **delegate parsing** to the scanners in `_custodian/`. So
`OCLogScanner` is shared code: Custodian's detectors use it via
`plugin_modules`; OC's ghost_audit imports it directly.

**OC's `maintenance/` CLIs** are nothing to do with Custodian. They're
operator tools (`recover_stale`, `cleanup_state`, `close_stale_prs`,
`check_regressions`, `triage_scan`, `cleanup_stale_backlog`) that
*mutate* Plane state. Audits never mutate; maintenance does.

## Three categories of "tool"

| Layer | Lives in | Mutates state? | Examples |
|---|---|---|---|
| **Audit** (read-only, codebase-only) | Custodian core + OC's `_custodian/` | No | C1–C8, OC1–OC9, AI1–AI4, DC1–DC5 |
| **Runtime audit** (read-only, hits live services) | OC's `entrypoints/{ghost,flow}_audit/` | No | ghost_audit, flow_audit |
| **Maintenance** (mutates Plane / git / state) | OC's `entrypoints/maintenance/` | Yes | recover_stale, cleanup_state, close_stale_prs, triage_scan, check_regressions |

## Adding new detectors

**For codebase audits** (read source files, count patterns):
add a function to one of the OC plugin modules and register it in
`build_oc_*_detectors()`. Choose the namespace by intent:

- `OC*` for code-health overlays (TODOs / settings / entrypoints)
- `AI*` for architecture invariants (import boundaries / layering)
- `DC*` for doc conventions (cross-references / front matter)

**For runtime audits** (need Plane / live state):
extend `entrypoints/ghost_audit/` or `entrypoints/flow_audit/`. These
are not Custodian-shaped — they get their own CLI and operator
invocation cadence.

**For maintenance** (mutates state):
new file under `entrypoints/maintenance/`, console-script in
`pyproject.toml`. Maintenance tools are NEVER detectors — Custodian's
audits must remain side-effect-free.

## Why this split

1. **Generic vs. specific** — Custodian's C1–C8 are repo-agnostic
   (TODOs, print, bare except). OC's overlays know things specific to
   OC's shape (settings.py field walking, entrypoints/ structure).
2. **Audit vs. maintenance** — Audits are observational; maintenance
   is operational. Mixing them creates an "audit accidentally triggered
   a Plane mutation" failure mode.
3. **Pure-code vs. live-service** — Custodian audits a checked-out
   tree. Runtime audits need a Plane token, GitHub token, and an
   active observation system. Different inputs = different shapes.
4. **Cross-repo reuse** — Custodian works for any consumer (SwitchBoard,
   OperatorConsole, WorkStation will get the same C1–C8 baseline plus
   their own `_custodian/` plugins). OC's ghost/flow audits only make
   sense for OC.

## Could runtime audits eventually move to Custodian?

Possibly — would need new protocols like `RuntimeStateScanner`
exposing Plane queries generically. That's a Custodian v0.2 design
decision, not a v0.1 task. Until then, runtime audits stay in OC.

## Files at a glance

| File | What it does |
|---|---|
| `.custodian.yaml` | Tells Custodian what plugins / detectors to load |
| `_custodian/__init__.py` | Empty — just makes it a package |
| `_custodian/detectors.py` | OC1–OC9 (code-health) |
| `_custodian/architecture.py` | AI1–AI4 (wraps `tools/audit/architecture_invariants/`) |
| `_custodian/doc_conventions.py` | DC1–DC5 (doc hygiene) |
| `_custodian/log_scanner.py` | `OCLogScanner` — parses watcher log lines |
| `_custodian/state_scanner.py` | `OCStateScanner` — knows OC's state-file layout |
| `tools/audit/architecture_invariants/` | Pre-existing checker, wrapped by AI1–AI4 |
| `src/operations_center/entrypoints/ghost_audit/` | Runtime audit, uses OCLogScanner |
| `src/operations_center/entrypoints/flow_audit/` | Runtime audit, uses OCStateScanner |
| `src/operations_center/entrypoints/maintenance/*` | Operator CLIs (mutate state) |
