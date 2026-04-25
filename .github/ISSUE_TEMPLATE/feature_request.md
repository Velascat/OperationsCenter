---
name: Feature Request
about: Suggest an improvement or new capability
labels: enhancement
assignees: ''
---

## Summary

A one-sentence description of the feature.

## Problem It Solves

What is currently difficult or impossible that this would fix?

## Proposed Solution

How you imagine it working. Include API or CLI examples if relevant.

## Affected Layer

Which part of OperationsCenter does this touch?

- [ ] Contracts
- [ ] Planning / proposer
- [ ] Routing (SwitchBoard client)
- [ ] Policy gate
- [ ] Adapter / execution
- [ ] Observability / artifact persistence
- [ ] Tuning / upstream eval
- [ ] Autonomy loop
- [ ] Operator tooling (scripts, CLI)

## Alternatives Considered

Other approaches and why you ruled them out.

## Invariant Check

Confirm this change preserves the execution boundary invariant:
- Policy is still enforced before adapter dispatch
- No alternate execution paths are introduced
- Failures remain explicit (no silent fallback)

## Additional Context

Related issues, architecture docs, or prior discussion.
