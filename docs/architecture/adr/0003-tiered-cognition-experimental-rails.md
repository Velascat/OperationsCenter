# ADR 0003 — Tiered cognition: experimental rails, not yet a runtime claim

## Status

Proposed (exploratory — sets up the rails to evaluate the pattern; does
not commit to running it in production until measured).

## Context

The architecture work in ADR 0001 (execution boundary) and ADR 0002
(backend card axes) characterized backends along permission, runtime,
topology, and mechanism axes. None of those answer a question that
becomes urgent the moment a workflow has more than one node:

> Does every node in a workflow need the same model strength?

The empirical answer is almost certainly no. A typical Archon workflow
shape decomposes into:

```text
plan        → strong model         (frontier cognition)
scan        → deterministic         (bash / grep)
implement   → bounded cognition     (small model viable)
validate    → deterministic         (tests / lint / type)
review      → strong model         (frontier cognition)
pr          → deterministic         (git / gh)
```

Only two of six nodes need frontier cognition. The rest are bounded
transforms, structure enforcement, or artifact movement. The
implementation node — typically the most token-intensive — is the
one most amenable to a smaller, cheaper, possibly local model.

**The non-obvious win is not parallelism or retries. It is**
**planning amortization.** A strong model plans once; many weaker
models execute bounded tasks beneath that plan. This is a different
economic regime from conversational multi-agent systems where every
agent re-plans the universe every turn.

### What the existing architecture already enables

The mechanism for tiered cognition is mostly already in place:

| Mechanism | Status |
|-----------|--------|
| `RuntimeBinding` per-invocation (model selectable per RuntimeInvocation) | exists |
| `aider_local` backend (Ollama-base-URL configurable) | exists |
| Archon workflow YAML per-node `provider:` / `model:` overrides | exists upstream |
| `ExecutionTrace.runtime_invocation_ref` (which model ran which node) | exists (ADR 0001 + G-V01) |
| `ExecutionTrace.routing` (which SwitchBoard rule chose this) | exists (G-V02 + G-V03) |
| **Per-node cost / token / latency telemetry** | **missing** |

The architecture is much closer to "run the experiment" than to
"design the experiment." The missing piece is the observability of
**economics**, not the orchestration capability.

### Why this matters for the broader architecture

Three axes the system already characterizes are all *per-backend*:

```text
capability   — what may this backend do?
runtime      — what runtime kinds can drive it?
topology     — how does this backend think? (ADR 0002)
mechanism    — how is this backend shipped? (ADR 0002)
```

Tiered cognition introduces a fourth axis at a *different layer*:

```text
cognition tier sensitivity — per-node, inside a workflow
```

This must not collapse into the per-backend cards. A backend like
`archon` does not have a tier — its **nodes** do. Encoding tier as a
backend attribute would re-introduce the combinatorial identity
collapse ADR 0002 explicitly bans (G2 / G3). The right home for
cognition tier is **workflow YAML**, not backend cards.

```text
backend identity   ≠   workflow shape   ≠   per-node cognition tier
       ↑                    ↑                       ↑
   ADR 0002 cards       Archon YAML          Archon node fields
                                              (provider, model)
```

That separation is the architectural discipline this ADR preserves.

## Decision

This ADR does **not** decide that tiered cognition will be the default.
It decides three things that prepare the ground for an honest decision
later:

### D1. Don't encode cognition tier in backend cards

A `cognition_tier_required` field on `capability_card.yaml` or
`mechanism_profile.yaml` would be wrong on its face — the attribute
varies per workflow node, not per backend. The discipline from ADR
0002 (one axis per card; subjective stays in `recommendations.md`)
applies: cognition tier is workflow-shaped, so it lives in the
workflow.

If cognition tier ever becomes a CxRP enum, it lives next to
`AgentTopology` and `ShippingForm` as a vocabulary primitive that
*workflow YAMLs* select from — never as a backend self-description.
**This ADR does not propose the enum.** Per G1 in ADR 0002, the
default answer to "should we add a vocabulary?" is no, until two
independent workflows are observed using it.

### D2. Land per-node cost / token / latency telemetry on the trace

Without this, the experiment can't be evaluated. The minimum surface:

```text
RuntimeInvocationRef gains:
  cost_usd            : Optional[float]   # adapter best-effort
  input_tokens        : Optional[int]
  output_tokens       : Optional[int]
  duration_ms         : Optional[int]     # already on RxP RuntimeResult
  model_id            : Optional[str]     # what the runtime actually used
                                           # (which may differ from what
                                           # RuntimeBinding requested — that
                                           # gap itself is an audit signal)
```

All optional. Adapters that don't know stay None — no synthesis. The
existing G-V01 wiring already populates `runtime_invocation_ref` from
adapters; this extends what's already populated, not where.

Aggregation lives on the trace, not on every record:

```text
ExecutionTrace gains:
  cognition_summary:
    total_cost_usd        : Optional[float]
    total_input_tokens    : Optional[int]
    total_output_tokens   : Optional[int]
    nodes_by_model        : dict[model_id, count]   # observed
    strong_node_count     : Optional[int]   # if classified
    bounded_node_count    : Optional[int]   # if classified
```

`operations-center-run-show` (Hardening arc item 3) renders this
block when present. `--json` emits it raw.

### D3. Keep the experiment honest with synthesized observation

The synthesized-card pattern from ADR 0002's deferred follow-up
applies here directly. After enough runs:

```text
declared per node:        provider/model in the Archon workflow YAML
observed per node:        runtime_invocation_ref.model_id from trace
declared overall shape:   "tiered" / "uniform"  (a workflow YAML hint)
observed overall shape:   inferred from nodes_by_model
```

Drift between declared and observed is a debug signal. A workflow
that declared `model: haiku` but the trace shows `model: sonnet`
under the wire means a fallback fired or the runtime ignored the
binding. That mismatch must be visible — it is exactly the failure
mode that makes tiered cognition silently regress to expensive.

## Guidelines

These are not as load-bearing as ADR 0002's discipline rules — this
ADR is exploratory. But they are guardrails to keep the experiment
from contaminating the architecture.

### G1. Separation of concerns is non-negotiable

```text
backend cards     → identity (per-backend, frozen vocabulary)
workflow YAML     → shape    (per-workflow, Archon-owned)
runtime telemetry → reality  (per-invocation, observed)
```

A change that smuggles cognition tier into backend cards is wrong;
revisit by writing the change as a workflow YAML field instead.

### G2. Cost telemetry is best-effort, not contract

Adapters that can't measure stay None. Don't synthesize a number. A
trace with `cost_usd=None` is honest; a trace with a guessed number is
worse than no trace. The G-V01 discipline (adapters that don't invoke
ExecutorRuntime leave `runtime_invocation_ref` None) is the precedent.

### G3. The experiment surface is the trace, not a new endpoint

Do not introduce `/api/cognition_metrics` or a metrics service. Cost
data lives on the same artifact (`execution_trace.json`) that already
carries provenance. Operators read both with the same tool
(`operations-center-run-show`). One artifact, one read path.

### G4. Don't optimize before measuring

This ADR enables measurement; it does not declare a routing rule. A
SwitchBoard rule that prefers `aider_local` for `implement` nodes is
a follow-up that requires evidence — at least 20 paired runs across
two non-trivial workflows where the cheap-tier variant produced
comparable success rate. That evidence does not exist yet.

## Implementation arc (proposed scope, not yet a backlog claim)

In order, gated on each step actually working:

1. **OC — extend `RuntimeInvocationRef`** with the four optional
   telemetry fields. Adapters that read them from their underlying
   runtime populate; others leave None. No CxRP changes (the fields
   live on OC's contract layer, mirroring the G-V01 pattern).
2. **OC — add `cognition_summary` to `ExecutionTrace`**. Aggregator
   walks the run's invocations and rolls up. Empty (`None` /
   `{}`) when no underlying data.
3. **OC — `operations-center-run-show` rendering** for the new block.
4. **OC — Archon adapter telemetry pass-through**. Archon's run-detail
   API exposes per-node token counts; surface them.
5. **(Optional, late)** A small experimental harness that runs the
   same workflow with two `provider:`/`model:` configurations and
   diffs the resulting cognition summaries. Not a permanent CLI —
   throwaway script under `experiments/`.

Out of scope:

- Adding a CxRP `CognitionTier` enum. (G1 in ADR 0002 — premature.)
- Routing rules that prefer cheap tiers. (D3 + G4 here.)
- Per-node cost gates / budget enforcement. (Requires per-node
  costs to first be reliable — probably one arc later.)
- A "tiered workflow" Archon YAML schema extension. (Archon's
  responsibility upstream; OC consumes whatever ships.)

## Consequences

- Trace artifacts grow modestly (the four-to-seven new fields are
  small). G-V03's "trace is self-contained" property is preserved
  and strengthened — operators can answer the cost question from
  the same artifact that already answers the provenance question.
- The synthesized-card pattern stays consistent across axes:
  declared (backend cards / workflow YAML) versus observed (trace
  telemetry) is the same diff shape everywhere it appears.
- The economic question — *was the cheap-tier run actually cheaper?
  what was the success-rate gap? where did the small model fail?* —
  becomes answerable from artifacts, not from running it twice in
  one's head.
- Local-model exploration (Ollama / vLLM behind `aider_local`)
  becomes architecturally first-class. The backend cards from
  ADR 0002 already mark `aider_local` as `single_agent` /
  `local_subprocess`; with this ADR's telemetry, comparing it
  head-to-head against `archon` for an `implement` node is a
  question of running the workflow twice and reading the trace.

## Non-goals (explicit)

- **A claim that tiered cognition saves money.** That claim requires
  evidence this ADR does not yet have.
- **A claim that small / local models are good enough for bounded
  nodes.** Genuinely empirical; one evening of experiments will
  clarify more than any amount of design.
- **Replacing strong models for non-bounded work.** Planning, review,
  cross-file reasoning, novel architecture decisions — frontier
  cognition continues to matter for those.

## Why this ADR exists at all

The architecture is unusually close to enabling tiered-cognition
experiments without realizing it. RuntimeBinding, the
`runtime_invocation_ref` linkage from G-V01, the trace richness from
G-V03, and the existing `aider_local` backend together cover most of
the surface. The remaining work is **observability of economics** —
landing cost/token/latency on the trace. Without it, the rails exist
but the dial doesn't.

This ADR locks that distinction in: rails ready, dial pending. The
empirical question — *should we route tiered, and where?* — is
deliberately left open for measurement to answer. Decisions before
measurement are how systems collect "wouldn't it be cool if" features.
