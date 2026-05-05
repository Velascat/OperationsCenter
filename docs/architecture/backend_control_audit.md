# Backend Control Audit, Catalog, and Fork Decision Framework

**Status:** Specification — ready for execution.
**Owner:** OperationsCenter.
**Related:** `recovery_loop_design.md`, CxRP contracts.

---

## Title

Build a backend control audit and executor catalog system that proves when
Kodo, Archon, and future agent frameworks require adapters, wrappers,
upstream patches, or forks.

---

## Objective

Create an evidence-driven system for deciding and justifying backend forks.

This task must establish:

- the CxRP 3-layer execution model
- a full contract foundation for runtime/capability/evidence control
- per-backend audit artifacts
- normalized backend results
- drift detection (global)
- objective capability/runtime cards
- a scoped v1 executor catalog/indexer
- fork/upstream-patch decision rules
- re-audit triggers

The result should convert:

> "We want forks"

into:

> "We have timestamped, queryable, enforceable proof of why this fork
> exists and what it fixes."

---

## Core Principle

Forks are not architecture. Forks are enforced control boundaries when
adapters cannot uphold CxRP invariants.

Adapters are first; forks are expected when control cannot be enforced
externally.

---

## System Model

CxRP must separate three decisions:

```text
Lane
  = what kind of work is this?

Executor / Backend
  = what system performs it?

RuntimeBinding
  = what powers it (CLI sub, local server, hosted API, container,
    human, or backend_default)?
```

Examples:

```text
architecture_design → kodo   → claude_cli / opus
repo_patch          → aider  → ollama / qwen-coder
workflow_agent      → archon → runtime binding TBD
human_review        → human  → no model runtime
```

Do not collapse lane, executor, and runtime into one field.

---

## Non-Negotiable Invariants

- Backends do NOT decide runtime policy.
- Backends do NOT silently escalate capabilities.
- Backends do NOT leak framework-specific result shapes past the adapter.
- OperationsCenter (OC) binds and validates execution.
- SwitchBoard recommends; OC enforces.
- All drift is detectable and queryable.
- Fork decisions must be backed by `contract_gaps.yaml`.
- Cards are objective, validated, and derived from real behavior.

---

# Phase 0 — CxRP Contract Foundation (BLOCKING)

⚠️ **NOTHING ELSE STARTS UNTIL THIS IS COMPLETE** ⚠️

Estimated effort: **2–3 sprints**.

## Required (in order)

1. Contract evolution policy (README)
2. Evidence.extensions
3. CapabilitySet enum
4. Capability guardrails + tests
5. RuntimeBinding (kind + selection_mode)
6. RuntimeBinding validity table (kind × selection_mode)
7. RuntimeBinding optional-field validation
8. Normalized ExecutionResult shape

## 0.1 Contract Evolution Policy

Add to CxRP README:

```text
Additive fields are non-breaking.
Renames/removals require schema_version bump.
Capability removals require deprecation cycle:
  deprecated → one release/version window → remove.
```

Also document:

- producers may add fields only when schemas permit them
- consumers must reject unknown fields unless the schema explicitly allows extension
- `evidence.extensions` is the designated extension slot for backend-specific evidence

## 0.2 Evidence.extensions

```yaml
evidence:
  extensions: {}
```

Rules:

- backend-specific data goes here
- contract-level fields do not become backend-specific dumping grounds
- extensions must be serializable
- extensions may be ignored by consumers

## 0.3 CapabilitySet Enum

Coarse capability enum only. Initial set:

```text
repo_read
repo_patch
test_run
shell_read
shell_write
network_access
human_review
```

Keep the set small. Do not encode quantity, size, strictness, path
scope, or limits into capability names.

## 0.4 Capability Guardrails

Capabilities are coarse permission classes. Policies carry quantifiers.

```text
repo_patch      ✅ capability
max_diff_lines  ✅ policy
allowed_paths   ✅ policy
repo_patch_safe ❌ policy leaked into capability
repo_patch_500  ❌ quantifier leaked into capability
```

Add guardrail tests rejecting capability names containing:

- numeric suffixes
- size words: `small`, `medium`, `large`
- degree words: `safe`, `strict`, `loose`, `limited`, `max`, `min`

Guardrail, not a substitute for PR review.

## 0.5 RuntimeBinding

```yaml
kind: backend_default | cli_subscription | local_model_server | hosted_api | containerized_runtime | human
selection_mode: backend_default | policy_selected | explicit_request
```

Optional fields:

```yaml
provider: <string>
model: <string>
endpoint: <string>
config_ref: <string>
```

Rules:

- `model` is optional because not every runtime exposes a direct model field
- CLI subscriptions and humans are runtime kinds, not model names
- backend defaults must be explicit, not implicit

## 0.6 RuntimeBinding Validity Table

`kind × selection_mode`:

```text
kind=backend_default     + selection_mode=backend_default     ✅
kind=cli_subscription    + selection_mode=explicit_request    ✅
kind=local_model_server  + selection_mode=policy_selected     ✅
kind=human               + selection_mode=backend_default     ❌
kind=backend_default     + selection_mode=explicit_request    ❌
```

Invalid combinations must fail validation before dispatch.

## 0.7 RuntimeBinding Optional-Field Validation

Each optional field has an explicit allow-list against `kind`. Anything
not on the list is rejected — no escape clauses, no implicit allows.

| Field        | Allowed kinds                                             |
|--------------|-----------------------------------------------------------|
| `model`      | `cli_subscription`, `local_model_server`, `hosted_api`    |
| `provider`   | `cli_subscription`, `hosted_api`                          |
| `endpoint`   | `local_model_server`, `hosted_api`                        |
| `config_ref` | `cli_subscription`, `local_model_server`, `hosted_api`, `containerized_runtime` |

`human` and `backend_default` carry none of these optional fields.

Invalid combinations must fail before dispatch.

## 0.8 Normalized ExecutionResult Shape

```yaml
result_id: <id>
request_id: <id>
ok: true | false
status: pending | accepted | running | succeeded | failed | cancelled | rejected | timed_out
summary: <string>
evidence:
  files_changed: []
  commands_run: []
  tests_run: []
  artifacts_created: []
  failure_reason: <string|null>
  extensions: {}
```

---

# Phase 1 — Minimal Adapter Discovery

## Goal

Discover real backend behavior without assumptions. Cards must not be
written from vibes — cards are outputs of integration discovery, not
inputs.

## Target Backends

Start with: **Kodo**, **Archon**.

Future: Claude CLI, Codex CLI, Aider/Ollama, CrewAI, LangGraph, AutoGen,
PraisonAI.

## Requirements

For each backend:

- invoke backend
- pass representative input
- capture raw output
- capture invocation metadata
- capture stdout/stderr/logs where available
- do not normalize yet
- do not enforce policy yet

## Sample Locations

```text
operations_center/executors/<backend>/samples/raw_output/*.json
operations_center/executors/<backend>/samples/invocations/*.json
```

## Exit Criteria

Collect either:

```text
≥10 runs
≥3 lane types OR backend-supported lane count, whichever is smaller
≥1 failure case
≥1 complex/multi-step case
```

OR coverage of all observed output shapes.

## Sample Safety

All samples MUST be scrubbed before commit.

Provide:

```text
operations_center/executors/_scrub.py
  def scrub_sample(raw) -> sanitized
```

Rules:

- remove secrets/tokens (e.g. `sk-`, `ghp_`)
- remove absolute home paths and usernames
- remove API keys from env / logs
- redact customer data

All sample writes must pass through `scrub_sample`. CI MUST scan
committed samples for high-entropy strings and common token prefixes.

---

# Phase 2 — Normalization

## Goal

Map raw backend outputs into the normalized CxRP `ExecutionResult` shape.

## Requirements

```text
operations_center/executors/<backend>/normalizer.py
```

Extracts: status, ok, summary, files_changed, commands_run, tests_run,
artifacts_created, failure_reason. All backend-specific data goes under
`evidence.extensions`.

## Enforcement Tests

Add unit tests asserting:

- normalized `ExecutionResult` contains no fields outside the CxRP schema
- backend-specific fields only appear under `evidence.extensions`
- malformed backend output either normalizes to a failed result or
  raises a typed normalization error

> No backend-specific shape leaks past this layer.

---

# System Phase — Drift Detection (GLOBAL)

This is a global OperationsCenter system phase, not a per-backend
implementation. Per backend, verify it works using synthetic drift tests.

## Location

```text
operations_center/drift/
```

## Shared Fixture

```text
operations_center/drift/testing.py::DriftInjectionFixture
```

Parameter knobs: `runtime`, `capability`, `output_shape`,
`internal_routing`. All backends MUST use this fixture for synthetic
drift tests — no per-backend reimplementation.

## Drift Examples

- backend used different runtime than bound
- backend requested or used unauthorized capability
- backend selected internal model/tool outside policy
- backend returned non-normalized result shape
- backend internal routing bypassed OC constraints

## Required Finding Type

```text
BACKEND_DRIFT
```

Payload:

```yaml
backend_id: <backend>
request_id: <request>
drift_type: runtime | capability | output_shape | internal_routing
observed: {}
bound_or_allowed: {}
impact: <summary>
```

## Ownership Rule

Adapters report what happened. OperationsCenter decides whether it is
drift.

## Per-backend Verification

Each backend must include synthetic drift tests for applicable types:

- **Runtime drift** — adapter reports a runtime different from bound;
  expect `BACKEND_DRIFT drift_type=runtime`
- **Capability drift** — adapter reports use of forbidden capability;
  expect `BACKEND_DRIFT drift_type=capability`
- **Output shape drift** — adapter returns a top-level field outside
  CxRP schema; expect `BACKEND_DRIFT drift_type=output_shape`
- **Internal routing drift** — multi-agent adapters; expect
  `BACKEND_DRIFT drift_type=internal_routing`

`drift_detection: PASS` in `audit_verdict.yaml` is only valid if
synthetic drift tests were observed firing correctly.

---

# Phase 3 — Runtime Control Audit

## Goal

Determine whether OC can bind runtime per request.

## Questions (answer with evidence)

- Can runtime/model be set per request?
- Is runtime selection process-global through env/config?
- Do internal agents override runtime?
- Can CLI subscription execution be forced?
- Can hosted/local runtime selection be disabled when policy forbids it?
- Can backend default selection be made explicit and auditable?

## Classification

```text
PASS    → per-request RuntimeBinding works
PARTIAL → wrapper/global/env/config workaround only
FAIL    → no reliable runtime control or overridden internally
```

## Short-Circuit Rule

If `FAIL`:

```text
Outcome is at least upstream_patch_pending or fork_required.
Later phases continue only in documentation mode.
```

Documentation mode = continue gathering evidence and updating
`contract_gaps.yaml`; do not continue trying to prove `adapter_only`.

---

# Phase 4 — Capability Control Audit

## Three Capability Axes

Do not collapse:

```text
backend.advertised_capabilities
request.required_capabilities
policy.allowed_capabilities
```

Validation:

```text
required_capabilities ⊆ (advertised_capabilities ∩ allowed_capabilities)
```

## Questions

- Can repo access be constrained?
- Can file patching be constrained?
- Can tool usage be restricted?
- Can shell access be bounded?
- Can network access be bounded?
- Are permissions explicit or implicit?
- Can backend attempts to use unauthorized capabilities be observed?

## Classification

```text
PASS    → enforceable externally
PARTIAL → wrapper-level enforcement or limited coverage
FAIL    → backend controls permissions internally
```

---

# Phase 5 — Failure Observability Audit

## Questions

- Are errors structured?
- Can failures be categorized?
- Is output deterministic enough to parse?
- Are stdout/stderr/logs available for evidence?
- Can timeout be distinguished from cancellation?
- Can model failure be distinguished from tool failure?
- Can backend infrastructure failure be distinguished from task failure?

## Classification

```text
PASS    → structured + classifiable
PARTIAL → partially opaque but workable
FAIL    → unstructured / unreliable / not observable
```

---

# Phase 6 — Internal Routing Audit

Critical for multi-agent backends (Archon, CrewAI, LangGraph, AutoGen,
PraisonAI). Single-agent backends may mark `N/A`.

## Questions

- Can OC pin the model used by every internal agent?
- Can OC restrict which tools each internal agent can call?
- Can OC observe which agent did which step?
- Can OC veto or abort an internal agent step before execution?
- Can internal roles be treated as private implementation details?
- Can internal traces be flattened into one ExecutionResult?
- Can internal role/team concepts be mapped from SwitchBoard lane intent
  without becoming CxRP contract fields?

## Classification

```text
PASS    → internal routing bounded and observable
PARTIAL → partially controllable
FAIL    → backend overrides OC policy or hides routing
N/A     → backend has no internal multi-agent routing
```

`N/A` is treated as PASS in the decision matrix.

---

# Phase 7 — Contract Gaps Artifact

```text
operations_center/executors/<backend>/contract_gaps.yaml
```

Format:

```yaml
- id: G-001
  gap: <description>
  discovered_at: <timestamp>
  backend_version: <version-or-unknown>
  impact: <what breaks>
  workaround: <current workaround>
  fork_threshold: <condition that forces fork>
  status: open | mitigated | patched_upstream | forked
  patch_deadline: <date-or-null>
```

Rules:

- Every compromise must be recorded.
- This file is the justification trail for forks.
- Fork decisions without matching gap entries are invalid.
- `upstream_patch_pending` requires at least one open gap with
  `patch_deadline`.
- `fork_required` requires at least one forked gap or a gap whose fork
  threshold has been met.

---

# Phase 8 — Capability and Runtime Cards

Cards are post-discovery artifacts. Do not write aspirational cards
before integration discovery.

## Required Files

```text
operations_center/executors/<backend>/capability_card.yaml
operations_center/executors/<backend>/runtime_support.yaml
```

## Hard Rule — Objective Only

Allowed in cards: backend_id, backend_version, advertised_capabilities,
measured constraints, supported runtime kinds, supported selection
modes, known runtime gaps, known capability gaps.

NOT allowed: `good_for`, `bad_for`, `strengths`, `weaknesses`,
marketing-style prose, subjective recommendations.

Subjective commentary goes here:

```text
operations_center/executors/<backend>/recommendations.md
```

## Example capability_card.yaml

```yaml
backend_id: kodo
backend_version: unknown
advertised_capabilities:
  - repo_read
  - repo_patch
  - test_run
measured_constraints:
  max_observed_files_changed: 8
  max_observed_commands_run: 4
known_capability_gaps:
  - G-003
```

## Example runtime_support.yaml

```yaml
backend_id: kodo
backend_version: unknown
supported_runtime_kinds:
  - cli_subscription
supported_selection_modes:
  - backend_default
  - explicit_request
known_runtime_gaps:
  - G-001
```

## Validation

Cards must validate against CxRP enums: `CapabilitySet`,
`RuntimeBinding.kind`, `RuntimeBinding.selection_mode`. Unknown values
must fail. Validation runs in CI, at OC process startup, before adapter
registration completes.

---

# Phase 9 — audit_verdict.yaml

```text
operations_center/executors/<backend>/audit_verdict.yaml
```

## Required Schema

```yaml
backend_id: kodo
audited_at: 2026-05-05
audited_against_cxrp_version: 1.2
backend_version: unknown
per_phase:
  runtime_control: PASS
  capability_control: PARTIAL
  drift_detection: PASS
  failure_observability: PASS
  internal_routing: N/A
outcome: adapter_plus_wrapper
gap_refs:
  - G-001
  - G-003
next_review_by: 2026-08-05
```

## Allowed Phase Values

```text
PASS | PARTIAL | FAIL | N/A
```

`N/A` is valid for phases that do not apply.

## Allowed Outcomes

```text
adapter_only
adapter_plus_wrapper
upstream_patch_pending
fork_required
```

## Rules

- `gap_refs` must reference IDs in `contract_gaps.yaml`.
- `outcome: upstream_patch_pending` requires at least one referenced
  open gap with `patch_deadline`.
- `outcome: fork_required` requires at least one referenced gap whose
  status is `forked` or whose `fork_threshold` has been met.

---

# Phase 10 — Executor Catalog / Indexer (V1)

Small in-memory truth source for SwitchBoard and OC. No query language
or ranking engine in v1.

## Location

```text
operations_center/executors/catalog/
  loader.py
  schema.py
  index.py
  query.py
```

Inside OperationsCenter — do not extract to a separate repo until a
second consumer exists.

## Inputs

```text
capability_card.yaml
runtime_support.yaml
contract_gaps.yaml
audit_verdict.yaml
```

## V1 Requirements

- load YAML files
- validate against CxRP enums at load time
- validate `audit_verdict.yaml` schema
- validate `gap_refs`
- fail-fast on invalid capability/runtime names
- fail-fast on invalid fork/upstream-patch verdicts
- build in-memory index

## V1 Queries (only these three)

### 1. Runtime Support Lookup

```python
catalog.backends_supporting_runtime(runtime_kind="cli_subscription")
```

### 2. Capability Match

```python
catalog.backends_supporting_capabilities(
    required_capabilities={"repo_read", "repo_patch"},
)
```

### 3. Verdict Lookup

```python
catalog.backends_by_outcome(outcome="fork_required")
```

## Not in v1

- ranking, scoring, benchmarking
- free-form query language
- database persistence
- subjective recommendations as routing input

## Purpose

The catalog is how SwitchBoard and OperationsCenter stop guessing. It
must route from validated facts, not prose.

---

# Phase 11 — Decision Matrix

Treat `N/A` as PASS for decision purposes.

```text
All PASS/N/A             → adapter_only
Any PARTIAL and zero FAIL → adapter_plus_wrapper
Any FAIL                 → upstream_patch_pending OR fork_required
```

## Upstream Patch Rule

Use `upstream_patch_pending` only when:

```text
average PR merge time < 30 days
OR last maintainer response < 14 days
```

AND:

```text
gap has fallback fork deadline
```

Otherwise:

```text
fork_required
```

## Fork Required Examples

Fork is required when backend cannot support:

- per-request RuntimeBinding
- OC-controlled capability restrictions
- normalized ExecutionResult
- reliable failure classification
- bounded internal multi-agent routing
- SwitchBoard / OC lane-executor-runtime injection

---

# Phase 12 — Enforcement Rules

## Catalog Loader Enforcement

The catalog loader must reject:

### Invalid Fork Verdict

```yaml
outcome: fork_required
```

unless `contract_gaps.yaml` contains a referenced gap with
`status: forked` or whose fork threshold has been met.

### Invalid Upstream Patch Verdict

```yaml
outcome: upstream_patch_pending
```

unless `contract_gaps.yaml` contains a referenced gap with
`status: open` and a `patch_deadline`.

## Fail Points

These validations must run:

- in CI
- at OC startup
- before adapter registration completes

No invalid backend card / verdict may remain silently loaded.

---

# Phase 13 — Re-Audit Triggers

Re-audit if ANY of:

- backend version changed
- CxRP RuntimeBinding schema changed
- CxRP CapabilitySet schema changed
- `audited_against_cxrp_version` < current minor version
- audit > 90 days old AND backend invoked in last 30 days

> Note: the `audited_against_cxrp_version` trigger overlaps with the
> RuntimeBinding/CapabilitySet schema-change triggers in most real
> bumps. The redundancy is intentional belt-and-suspenders — keep both
> so a missed enum-change ticket can't silently leave verdicts stale.

When a re-audit trigger fires:

- mark backend audit stale
- avoid using stale verdict for new routing decisions unless explicitly
  allowed by policy
- create / update audit task entry

---

# Initial Backend Expectations

## Kodo

Expected near-term outcome: `adapter_plus_wrapper`.

Notes:

- Already supports CLI subscription trick through team config.
- Can likely run Claude / Codex CLI backends.
- OC does not yet cleanly own / bind runtime choice.
- Needs RuntimeBinding integration, adapter normalization, objective
  cards, catalog entry.

Likely first proof target:

```text
architecture_design → kodo → claude_cli / opus
```

## Archon

Expected near-term outcome: `upstream_patch_pending OR fork_required`.

Notes:

- Current adapter is transport-shaped, not binder-shaped.
- No confirmed per-request runtime/model/provider parameter.
- Must spike whether Archon exposes per-workflow LLM override.
- If only env / global config exists → wrapper / fork risk is high.
- If internal agents choose runtime independently → fork is required.

Likely first proof target:

```text
architecture_design → archon → claude_cli / opus
```

This likely fails today unless Archon exposes per-workflow runtime
binding.

---

# Special Use Case to Prove

```text
SwitchBoard selects architecture_design
OperationsCenter binds executor = kodo OR archon
OperationsCenter binds RuntimeBinding = claude_cli / opus
Backend executes without overriding runtime / capabilities
ExecutionResult returns normalized evidence
Catalog records backend verdict and capability/runtime truth
```

⚠️ This proof target is reachable only AFTER Phase 0 completes.
Estimated start: **≥ 6 weeks from kickoff**.

Expected near-term path:

```text
Kodo proves first.
Archon determines whether fork is required.
```

---

# Deliverables Per Backend

```text
operations_center/executors/<backend>/adapter.py
operations_center/executors/<backend>/normalizer.py
operations_center/executors/<backend>/samples/raw_output/*.json
operations_center/executors/<backend>/samples/invocations/*.json
operations_center/executors/<backend>/contract_gaps.yaml
operations_center/executors/<backend>/capability_card.yaml
operations_center/executors/<backend>/runtime_support.yaml
operations_center/executors/<backend>/audit_verdict.yaml
operations_center/executors/<backend>/recommendations.md
```

Global / system deliverables:

```text
operations_center/drift/
operations_center/drift/testing.py::DriftInjectionFixture
operations_center/executors/_scrub.py
operations_center/executors/catalog/
CxRP RuntimeBinding contracts
CxRP CapabilitySet contracts
CxRP Evidence.extensions
CxRP ExecutionResult normalization contract
CxRP contract evolution README updates
```

---

# Success Criteria

For each backend, the system can answer with evidence:

- Can OC fully control runtime selection?
- Can OC constrain capabilities?
- Can SwitchBoard lane intent survive into execution?
- Can internal framework / team / role concepts be controlled or flattened?
- Does the backend require adapter, wrapper, upstream patch, or fork?
- Which contract gaps justify the verdict?
- Can the catalog / indexer route future work based on verified backend
  facts?

---

# Final Outcome

This system converts:

```text
We want forks
```

into:

```text
We have timestamped, queryable, enforceable proof of why forks exist,
what they fix, and how SwitchBoard / OperationsCenter should use them.
```

Ship this foundation before expanding beyond Kodo and Archon.
