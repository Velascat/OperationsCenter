# ADR 0002 — Backend self-description card axes

## Status

Proposed

## Context

Each runtime backend in OperationsCenter ships a small folder of YAML
files at `src/operations_center/executors/<backend>/` that describes
the backend to the rest of the system:

- `capability_card.yaml` — answers "what may this backend do?"
  (CxRP `CapabilitySet` enum: `repo_read`, `repo_patch`, `test_run`,
  `shell_read`, `shell_write`, `network_access`, `human_review`)
- `runtime_support.yaml` — answers "what runtime kinds can drive it?"
  (`cli_subscription`, `hosted_api`, etc.)
- `recommendations.md` — free-text operator guidance, not parsed.
- `contract_gaps.yaml` / `audit_verdict.yaml` — known issues + verdict.

The cards are deliberately disciplined:

- CxRP's capability enum bans degree, size, and quantifier tokens
  (`safe`, `large`, `max`, etc.) in capability *names* —
  `tests/unit/contracts/test_capability_naming.py` enforces it.
- The card loader rejects subjective fields (`good_for`, `bad_for`,
  `strengths`, `weaknesses`) and pushes them to `recommendations.md`.
- One axis per card.

Today's cards capture **permissions** ("may?") and **runtime kinds**
("can be driven by?"). They do **not** capture two attributes that
demonstrably differ between backends and that SwitchBoard's routing
rules can only reason about implicitly:

- **Agent topology** — `direct_local` and `aider_local` are
  single-agent; `kodo` is a hierarchical multi-agent crew (orchestrator
  delegates to architect/worker/reviewer in dialogue); `archon` is a
  declarative DAG of single-agent nodes. Topology determines call
  volume, debuggability, and whether parallel runs interact safely.
- **Mechanism / shipping form** — `kodo` wraps a Claude Code Max
  subscription CLI; `archon` is a long-running HTTP service with
  worktree isolation; `direct_local` is a local subprocess. Mechanism
  determines quota interaction, deploy footprint, and concurrency
  ceiling.

These attributes live today in `recommendations.md` prose, where
nothing reads them. As a result, SwitchBoard cannot ask "prefer a
single-agent DAG over a multi-agent crew for fast turnaround" — it can
only key off lane name, which is a backend-name proxy in disguise.

## Decision

Add **two new structured cards on two new axes**, preserving the
existing one-axis-per-card discipline:

```text
src/operations_center/executors/<backend>/
├── capability_card.yaml         # existing — permissions
├── runtime_support.yaml         # existing — runtime kinds
├── orchestration_profile.yaml   # NEW — agent topology
├── mechanism_profile.yaml       # NEW — shipping form
├── recommendations.md           # existing — subjective prose
├── contract_gaps.yaml           # existing
└── audit_verdict.yaml           # existing
```

### Vocabulary (CxRP enums)

Both new vocabularies live in CxRP, alongside `CapabilitySet` and
`RuntimeKind`. CxRP owns vocabulary; OC reads from it; backends select
from it.

```python
# cxrp/vocabulary/agent_topology.py
class AgentTopology(str, Enum):
    SINGLE_AGENT            = "single_agent"
    SEQUENTIAL_MULTI_AGENT  = "sequential_multi_agent"
    DAG_WORKFLOW            = "dag_workflow"
    SWARM_PARALLEL          = "swarm_parallel"

# cxrp/vocabulary/shipping_form.py
class ShippingForm(str, Enum):
    LOCAL_SUBPROCESS        = "local_subprocess"
    LONG_RUNNING_SERVICE    = "long_running_service"
    MANAGED_CLI             = "managed_cli"
    HOSTED_API              = "hosted_api"
```

Four values each. The same naming guardrails as `CapabilitySet`
apply: no degree tokens, no size tokens, no numeric suffixes.

### Card schema (mirrors capability_card.yaml)

```yaml
# orchestration_profile.yaml
backend_id: <name>
backend_version: <version | "unknown">
agent_topology: <enum value>
known_topology_gaps: []          # gap IDs, like contract_gaps

# mechanism_profile.yaml
backend_id: <name>
backend_version: <version | "unknown">
shipping_form: <enum value>
known_mechanism_gaps: []
```

Strict loader validation, mirroring `load_capability_card`:
- Reject any field outside the documented set.
- Reject any value not in the CxRP enum.
- Continue to reject the same `_DISALLOWED` subjective tokens.

### Initial assignments

`src/operations_center/executors/` today carries cards only for
`archon` and `kodo` (Phase 8 rollout). The new axes ship for those
two first; the remaining backends acquire their first card folder
the next time someone audits them, or under a later wholesale-rollout
arc — whichever comes first.

| Backend | folder exists today | agent_topology | shipping_form |
|---------|--------------------:|----------------|---------------|
| kodo         | ✓ | `sequential_multi_agent` | `managed_cli` |
| archon       | ✓ | `dag_workflow` | `long_running_service` |
| direct_local | ✗ | `single_agent` (planned) | `local_subprocess` (planned) |
| aider_local  | ✗ | `single_agent` (planned) | `local_subprocess` (planned) |
| openclaw     | ✗ | `single_agent` (planned) | `local_subprocess` (planned) |
| demo_stub    | ✗ | `single_agent` (planned) | `local_subprocess` (planned) |

Even with only the two existing card folders, both axes already
carry information: `kodo` and `archon` differ on every axis the
cards capture (capabilities, runtime kinds, topology, mechanism).

The "two-backend test" (G2 below) is met for **shipping_form**
immediately — `local_subprocess` is shared by every uncarded backend.
For **agent_topology** the test is met as soon as a third backend
acquires a card; until then `kodo` and `archon` each hold a unique
topology value, which is acceptable bootstrap state but not a
steady state. The remaining four backends should acquire cards
within the same operational arc.

### Synthesized siblings (deferred, but planned-for)

`capability_card.synthesized.yaml` already exists for the capability
axis. The same pattern extends to the new axes:

```text
orchestration_profile.synthesized.yaml   # observed topology
mechanism_profile.synthesized.yaml       # observed shipping form
```

Both axes can be derived from observed runs once G-V01 lands enough
runtime_invocation_refs in the trace stream — e.g. number of distinct
`invocation_id`s per OC run distinguishes `single_agent` from
`sequential_multi_agent`. Declared-vs-observed mismatch is a debug
signal: drift, regression, hidden escalation. This is what makes the
card layer eventually function as a **runtime truth reconciliation
layer** — not just declarations, but declarations a synthesizer can
diff against reality.

This ADR scopes the *declared* cards. Synthesis is its own follow-up.

### How SwitchBoard uses this

SwitchBoard's routing decision is a 3-tuple
`(selected_lane, selected_backend, runtime_binding)`. Today it
reasons over lane + task_type + risk_level. With the new axes
available, routing rules can express coherence checks structurally:

```python
# pseudocode of a rule SwitchBoard could now express
if topology == AgentTopology.SWARM_PARALLEL \
   and shipping_form == ShippingForm.MANAGED_CLI:
    # subscription quotas don't survive swarms — reject the combo
    raise IncoherentRoutingDecision(...)
```

Crucially: rules read the *axes*, not the backend names. The system
slides toward `if topology == DAG_WORKFLOW:` and away from
`if backend == "archon":`. That's the architectural inflection point
— identity moves from prose to enum, and routing scales with the
backend roster instead of rotting under it.

## Guidelines (the discipline that has to hold for this to pay off)

These are non-negotiable because the existing cards only stay useful
because the analogous discipline already holds for `CapabilitySet` and
`RuntimeKind`. Relaxing any of them collapses the whole system back
into backend-vibes spaghetti.

### G1. Resist enum proliferation

Start with the four values per axis listed above. If a fifth seems
necessary, the burden of proof is on the proposer to show:

- two existing or near-term backends share the new value
  (otherwise it is a backend-name synonym; see G2),
- the new value cannot be expressed as a *combination* of existing
  values plus another axis,
- the value name passes the same naming guardrails as
  `CapabilitySet` (no degree, no size, no quantifier).

The default answer to "should we add a value?" is **no**.

### G2. The "two-backend test" — no value may equal a backend name

Every enum value must be shared by at least two backends, present or
realistically near-term. If `agent_topology: dag_workflow` only ever
appears on `archon`, the axis carries zero information beyond
`backend_id` and the card is dead weight. The check is mechanical:

```text
for each enum value V across all backend cards:
    if V is used by exactly one backend:
        flag for review
```

### G3. The card stays factual; subjectivity stays in prose

Forbidden in `orchestration_profile.yaml` /
`mechanism_profile.yaml`:

- "best for" / "good for" / "should be used when" — operator guidance,
  belongs in `recommendations.md`.
- "preferred over X" — a routing rule, belongs in SwitchBoard policy.
- "reliable" / "experimental" — a degree, banned by naming guardrails.

The loader enforces this with the same `_DISALLOWED` set used by
`load_capability_card`. New cards carry the same banlist.

### G4. Cross-repo sequencing — vocabulary first

Adding an axis is a cross-repo change. The order is:

1. **CxRP** — define the enum with deprecation policy from day one.
   Add naming-guardrail tests.
2. **CxRP** — release a tagged version (semver minor; no breaking).
3. **OperationsCenter** — bump the CxRP pin, ship the loader, add
   the cards for existing backends.
4. **OperationsCenter** — extend `executors/catalog/query.py` so
   `backends_supporting_topology(...)` becomes queryable for routing.
5. **SwitchBoard** — only after steps 1-4 — start consuming the new
   axes in routing rules.

Defining the enums in OC instead of CxRP fragments the vocabulary
across repos and breaks cross-system reuse. The hard rule: **OC may
read CxRP vocabulary; OC must not invent it.**

## Layering reinforced

| Layer | Owns | Reads |
|-------|------|-------|
| CxRP | Vocabulary (enums, schemas) | — |
| Backend self-description (OC executors/) | Per-backend cards | CxRP |
| OperationsCenter | Loaders, catalog queries, routing-rule evaluation | Cards + CxRP |
| SwitchBoard | Routing decisions | Catalog + decision context |
| `recommendations.md` | Free prose for humans | — (not machine-read) |

No layer leakage. Cards are factual. Recommendations are subjective.
Routing rules are policy. Vocabulary is shared.

## Consequences

- Two new files per backend (`orchestration_profile.yaml`,
  `mechanism_profile.yaml`) — six existing backends ⇒ 12 new files.
- One new module each in CxRP for the enums, plus naming-guardrail
  tests mirroring `test_capability_naming.py`.
- `executors/_artifacts.py` gains `OrchestrationProfileCard` and
  `MechanismProfileCard` dataclasses + loaders + the same
  `_DISALLOWED` enforcement.
- `executors/catalog/query.py` gains `backends_with_topology(...)`
  and `backends_with_shipping_form(...)` helpers.
- SwitchBoard does not need to change for this ADR to land. Routing
  rules that exploit the new axes are a follow-up.
- The `recommendations.md` files for existing backends should be
  re-read; anything they describe that is now an enum value should be
  removed from prose to avoid drift between card and prose claims.
- This decision is stable in the sense that **adding axes** is the
  intended evolution path; **removing axes** would require a new ADR
  with explicit evidence that the axis stopped doing routing work.

## Non-goals

- Capturing model identity (`claude-3.5-sonnet` vs `gpt-5`).
  Model is per-invocation, not per-backend; lives in
  `RuntimeBinding`, not in cards.
- Capturing performance numbers (latency, token cost). Those vary
  per task; they belong in measured telemetry, not declared cards.
- Replacing `recommendations.md`. Prose stays exactly as it is, as
  the dump-ground for everything that doesn't fit a structured
  card. The card system grows by *promoting* observations from
  prose to enum, never by *demoting* prose to the card folder.

## Out-of-scope follow-ups (for backlog, not this ADR)

- Synthesized siblings — declared-vs-observed reconciliation.
- A boundary tool akin to
  `tools/boundary/switchboard_denylist.py` that fails CI when
  `recommendations.md` mentions a string that's already an enum
  value (drift detection between prose and card).
- A SwitchBoard rule that rejects incoherent
  `(topology, shipping_form, runtime_kind)` combinations.
