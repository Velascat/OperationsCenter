# Managed Repo Run Identity

**Phase:** 4  
**Module:** `operations_center.run_identity`  
**Related:** [Managed Repo Contract (Phase 1)](../../architecture/videofoundry_managed_repo_contract.md) · [Audit Toolset Contract (Phase 3)](managed_repo_audit_toolset_contract.md)

---

## Purpose

Phase 4 defines how OperationsCenter generates, validates, and propagates a unique identity for each managed audit run. A `run_id` is the anchor for lifecycle tracking: it ties the invocation request, the VF audit process, and the output files together.

This phase does not execute commands. It does not write manifests. It makes managed audit invocations traceable before Phase 6 dispatch runs them.

---

## Relationship to Phase 1 Managed Repo Config

The managed repo config (`config/managed_repos/videofoundry.yaml`) declares:

```yaml
run_id:
  source: operations_center
  env_var: AUDIT_RUN_ID
  format: uuid_hex
  required_for_managed_runs: true
```

Phase 4 reads `run_id.env_var` from this config so the env var name is always authoritative from config, not hardcoded at call sites. `generate_managed_run_identity_from_config()` performs this lookup automatically.

---

## Relationship to Phase 3 Invocation Requests

Phase 3 (`audit_toolset`) defines `ManagedAuditInvocationRequest`, which requires `AUDIT_RUN_ID` in its `env` field. Phase 4 is the layer that creates the run_id and injects it.

The integration flow:

```
Phase 4: generate ManagedRunIdentity
Phase 4: apply_run_identity_env → env dict with AUDIT_RUN_ID
Phase 3: resolve_invocation_request(run_id=identity.run_id, extra_env=env)
Phase 4: PreparedManagedAuditInvocation(identity, request)
Phase 6: dispatch (reads PreparedManagedAuditInvocation, executes command)
```

`prepare_managed_audit_invocation()` performs this entire flow and returns a `PreparedManagedAuditInvocation` without executing anything.

---

## Run ID Authority

```
OperationsCenter is the sole authority for managed run_id.
```

VideoFoundry has a local fallback that generates its own id when `AUDIT_RUN_ID` is not set (for dev/local runs). OpsCenter must never rely on that fallback. Every OpsCenter-initiated managed run injects `AUDIT_RUN_ID` before the command starts.

---

## Run ID Format

```
{repo_id}_{audit_type}_{YYYYMMDDTHHMMSSz}_{8hex}
```

Examples:

```
videofoundry_representative_20260426T164233Z_a1b2c3d4
videofoundry_stack_authoring_20260426T164233Z_b5c6d7e8
videofoundry_enrichment_20260426T164233Z_c9d0e1f2
```

| Property | Value |
|----------|-------|
| Path-safe | Yes — `[a-zA-Z0-9_]` only |
| Log-safe | Yes — printable ASCII, no shell-special chars |
| JSON-safe | Yes — valid JSON string value |
| Unique | Timestamp (second resolution) + 4 bytes random entropy (`secrets.token_hex(4)`) |
| Traceable | repo_id and audit_type embedded for human readability |
| Stable | Once generated, never mutated |

The format is validated by `is_valid_run_id(run_id)` and enforced by the `ManagedRunIdentity` Pydantic field validator.

**Format decision:** The recommended format from the Phase 4 spec was adopted verbatim. The uppercase `T` and `Z` are ISO 8601 timestamp separators and are path-safe (no shell or filesystem special meaning). The suffix uses `secrets.token_hex(4)` for cryptographic randomness without dependencies on VF internals.

---

## ManagedRunIdentity Model

`ManagedRunIdentity` is the Pydantic model representing the identity record.

| Field | Type | Description |
|-------|------|-------------|
| `repo_id` | `str` | Managed repo identifier (non-empty) |
| `audit_type` | `str` | Audit type (non-empty) |
| `run_id` | `str` | Generated run_id matching the approved format |
| `created_at` | `datetime` | UTC-aware creation timestamp |
| `env_var` | `str` | Env var name for injection; defaults to `"AUDIT_RUN_ID"` |
| `metadata` | `dict` | Arbitrary caller-supplied metadata |

Validators enforce:
- `repo_id` and `audit_type` are non-empty
- `run_id` matches `_RUN_ID_PATTERN`
- `created_at` is timezone-aware

---

## ENV Injection

`apply_run_identity_env(base_env, identity, *, allow_same=True)` injects the run_id into a copy of `base_env`:

```python
env = apply_run_identity_env({}, identity)
# → {"AUDIT_RUN_ID": "videofoundry_representative_20260426T164233Z_a1b2c3d4"}
```

Rules:
- Returns a new dict — `base_env` is never mutated
- All existing keys in `base_env` are preserved
- `identity.env_var = identity.run_id` is added
- If `env_var` already exists with a different value → `RunIdentityEnvConflictError`
- If `env_var` already exists with the same value and `allow_same=True` → accepted (idempotent)
- If `env_var` already exists with the same value and `allow_same=False` → `RunIdentityEnvConflictError`

---

## Conflict Policy

When `AUDIT_RUN_ID` is already present in the env with a different value, OpsCenter raises `RunIdentityEnvConflictError`. This is explicit and intentional:

- Silently overwriting a run_id from a different invocation context would break lifecycle tracking
- The caller must clear the conflicting value before injecting a new identity
- This situation should not arise in normal Phase 6 dispatch (each invocation starts from a clean env or one built by `prepare_managed_audit_invocation`)

---

## Invocation Preparation Flow

`prepare_managed_audit_invocation(repo_id, audit_type)` is the canonical end-to-end helper:

```python
from operations_center.run_identity import prepare_managed_audit_invocation

prepared = prepare_managed_audit_invocation(
    "videofoundry",
    "representative",
    metadata={"channel_slug": "Connective_Contours"},
)

# prepared.identity  → ManagedRunIdentity
# prepared.request   → ManagedAuditInvocationRequest (Phase 3)
# prepared.request.env["AUDIT_RUN_ID"] == prepared.identity.run_id
```

Internal steps:
1. Load managed repo config → read `run_id.env_var`
2. `generate_managed_run_identity_from_config()` → `ManagedRunIdentity`
3. `apply_run_identity_env({}, identity)` → env dict with `AUDIT_RUN_ID`
4. `resolve_invocation_request(run_id, extra_env=env)` → `ManagedAuditInvocationRequest`
5. Return `PreparedManagedAuditInvocation(identity, request)`

`PreparedManagedAuditInvocation` has `.identity`, `.request`, and `.metadata` attributes. Phase 6 dispatch receives this object and runs the command.

---

## Collision / Idempotency Policy

Generated run_ids are unique by design: the `YYYYMMDDTHHMMSSz` component gives second-level resolution and `secrets.token_hex(4)` adds 32 bits of random entropy. The probability of collision in the same second across reasonable workloads is negligible.

OpsCenter does not:
- Reuse run_ids for distinct managed runs
- Check the filesystem for collisions (deferred to Phase 6 lifecycle management)
- Implement repo locks or one-audit-at-a-time enforcement (Phase 6)

If a caller supplies a run_id (not using `generate_managed_run_identity`), `ManagedRunIdentity` validation enforces the format — plain UUID hex values are rejected.

---

## Local Producer Fallback Rule

VideoFoundry generates a fallback run_id when `AUDIT_RUN_ID` is not set in the environment (for local/dev runs). OpsCenter must never rely on this fallback:

```
For managed runs initiated by OpsCenter:
    AUDIT_RUN_ID is always set before invocation.
    OpsCenter reads run_id from run_status.json and verifies it matches.

For local/dev runs outside OpsCenter:
    VF may generate its own id.
    These runs are not OpsCenter-managed.
    OpsCenter does not ingest these without a reconciliation step (future phase).
```

---

## Non-Goals

- Phase 4 does not execute audit commands.
- Phase 4 does not spawn subprocesses.
- Phase 4 does not write `run_status.json` or `artifact_manifest.json`.
- Phase 4 does not implement repo locking or one-audit-at-a-time enforcement.
- Phase 4 does not implement artifact indexing.
- Phase 4 does not import VideoFoundry Python code.
- Phase 4 does not implement collision detection against the filesystem.
