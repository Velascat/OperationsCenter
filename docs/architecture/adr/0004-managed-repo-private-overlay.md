# ADR 0004 — Managed-Repo Config Private Overlay Pattern

## Status

Accepted

## Context

OperationsCenter orchestrates audit runs against managed repos. Each managed
repo requires a binding config that carries concrete details: filesystem path,
run-ID injection variables, capability list, and per-audit-type invocation
commands.

Two design forces pull in opposite directions:

1. **Privacy invariant (B1):** OC is a public repository. It must never commit
   content that names a private managed repo (e.g. VideoFoundry). Violating this
   leaks the private repo's existence into the public git history forever.

2. **Operator completeness:** OC still needs a concrete, validated config to
   dispatch against a managed repo at runtime. An empty template is not enough.

A third force is **schema drift prevention:** when `ManagedRepoConfig` gains a
new Pydantic field, every operator with a local config needs to know — silently
missing fields cause runtime failures or silent misconfiguration.

## Decision

Two-tier config lookup with a gitignored local overlay:

```
config/managed_repos/
  example_managed_repo.yaml   ← tracked template; names no private repos
  local/                      ← gitignored; contains per-operator bindings
    *.yaml                    ← concrete configs, one per managed repo
```

`loader.py` queries `local/` first, falling back to the tracked example only
for the canonical `example_managed_repo` entry (used in tests and documentation).
All files are validated via `ManagedRepoConfig.model_validate()` at load time.

The **plumbing contracts** (which files each repo writes and which paths OC
reads) are declared in the **writer's** `.custodian/config.yaml`, not in OC.
For a private managed repo, that means the contract lives in the private repo's
custodian config, with `reader_path` pointing to the public OC file. OC never
gains knowledge of the private repo's name through any tracked surface.

## Consequences

**Benefits:**
- B1 privacy invariant is structurally enforced: `local/` is gitignored by
  design, so a private repo name can never accidentally enter OC's git history.
- Schema validation at load time catches field mismatches early.
- OC11 (schema-sync detector) flags any Pydantic field added to `models.py`
  that is not yet represented in `example_managed_repo.yaml`.
- The tracked example serves as a migration guide: operators diff against it
  when upgrading an existing local config.

**Costs / limitations:**
- Operators must manually maintain `local/*.yaml` on each deployment machine.
  There is no automated sync; the operator is responsible for applying model
  changes to their local files after upgrading OC.
- CI cannot validate private-repo bindings. Integration tests run against the
  example config only.
- The two-tier lookup is implicit: new contributors may not realize `local/`
  exists until they read this ADR or the troubleshooting guide.

## Alternatives Considered

**A. Encrypted secrets in the tracked repo** — rejected because encryption
management adds operational complexity and still embeds metadata (e.g. the
encrypted file name `video_foundry.yaml.enc`) in git history.

**B. External config store (e.g. Vault, AWS SSM)** — rejected as over-engineering
for a small operator ecosystem. The local overlay achieves the same isolation
without a new dependency.

**C. Runtime environment variable injection** — rejected because the full
`ManagedRepoConfig` schema is too rich to flatten into env vars without losing
type safety and Pydantic validation.

## Related

- `config/managed_repos/example_managed_repo.yaml` — the tracked template
- `src/operations_center/managed_repos/models.py` — Pydantic schema
- `src/operations_center/managed_repos/loader.py` — two-tier lookup implementation
- `docs/operator/managed_repo_troubleshooting.md` — operator runbook
- `.custodian/config.yaml` (privacy block) — B1 enforcement configuration
