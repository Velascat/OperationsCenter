---
campaign_id: 10c50210-1304-4c47-a700-1dc340e4e85c
slug: cxrp-backend-card-vocabulary
phases:
  - implement
  - test
  - improve
repos:
  - CxRP
area_keywords:
  - vocabulary
  - contracts
status: active
created_at: 2026-05-08T19:05:42.731001+00:00
---

## Overview

Ship the two new vocabulary enums (`AgentTopology` and `ShippingForm`) in CxRP as specified by OperationsCenter ADR 0002. This is the prerequisite Phase 1 that unblocks the Backend Card Axis Expansion arc: OperationsCenter cannot define orchestration_profile or mechanism_profile cards until CxRP owns the canonical vocabulary. The work follows the established `(str, Enum)` pattern and naming-guardrail test convention already proven by `CapabilitySet`.

## Goals

1. **Add `AgentTopology` enum** — Create `cxrp/vocabulary/agent_topology.py` with four members: `SINGLE_AGENT`, `SEQUENTIAL_MULTI_AGENT`, `DAG_WORKFLOW`, `SWARM_PARALLEL`. Follow the `(str, Enum)` dual-inheritance pattern from `capability.py`. Export from the vocabulary package.

2. **Add `ShippingForm` enum** — Create `cxrp/vocabulary/shipping_form.py` with four members: `LOCAL_SUBPROCESS`, `LONG_RUNNING_SERVICE`, `MANAGED_CLI`, `HOSTED_API`. Same pattern as above. Export from the vocabulary package.

3. **Add naming-guardrail tests for both enums** — Mirror the `test_capability.py` pattern: enforce lowercase-snake-case values, reject numeric suffixes, reject size words, reject degree words. One test file per enum (`test_agent_topology.py`, `test_shipping_form.py`).

4. **Bump version to 0.3.0 and update CHANGELOG** — Semver minor bump (new public API surface). Add a Keep-a-Changelog entry under `[0.3.0]` documenting both new enums. This tagged release is what OperationsCenter will pin against.

## Constraints

- **Follow existing conventions exactly** — `(str, Enum)` base, `_value_` as lowercase snake_case string, module-per-enum under `cxrp/vocabulary/`.
- **No contract schemas yet** — This campaign adds vocabulary only. Card schemas (`orchestration_profile`, `mechanism_profile`) belong to the OperationsCenter Phase 2 campaign.
- **ADR 0002 guardrail G1** — Four values per axis maximum at launch. New values require proof that two backends share the value.
- **ADR 0002 guardrail G2** — No enum value may equal a single backend's name (the "two-backend test").
- **Do not modify existing enums** — `BackendName`, `ExecutorName`, `CapabilitySet`, `RuntimeKind`, etc. are stable; leave them untouched.
- **Python ≥3.11** — Match the existing `pyproject.toml` requirement.

## Success Criteria

- `pytest` passes with ≥4 new naming-guardrail tests per enum (8+ new tests total).
- `from cxrp.vocabulary.agent_topology import AgentTopology` and `from cxrp.vocabulary.shipping_form import ShippingForm` resolve correctly.
- Each enum round-trips through `str()` and reconstructs via `AgentTopology("single_agent")`.
- `pyproject.toml` reads `version = "0.3.0"` and CHANGELOG has a matching `[0.3.0]` section.
- `ruff check` and any existing CI lint gates remain clean.
- No changes outside `cxrp/vocabulary/`, `tests/`, `pyproject.toml`, and `CHANGELOG.md`.