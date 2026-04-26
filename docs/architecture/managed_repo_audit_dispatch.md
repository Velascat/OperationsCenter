# Managed Repo Audit Dispatch

**Phase**: 6  
**Package**: `src/operations_center/audit_dispatch/`  
**CLI**: `operations-center-audit`

---

## Purpose

Phase 6 is the first true runtime orchestration phase. It lets OperationsCenter execute a managed repo audit as an external subprocess while preserving all boundaries established in Phases 1–5.

The dispatcher:
1. Prepares a run identity and validated invocation request (Phases 4 and 3).
2. Enforces the one-audit-per-repo policy via an in-memory lock.
3. Executes the configured command as an external process.
4. Captures stdout and stderr to log files.
5. Discovers `run_status.json` after process exit using the bucket's run_id.
6. Resolves `artifact_manifest_path` through the Phase 3 discovery chain.
7. Returns a structured `ManagedAuditDispatchResult`.

Phase 6 does **not** index artifacts, harvest fixtures, implement slice replay testing, or import VideoFoundry code.

---

## Relationship to Phase 1 Managed Repo Config

`ManagedRepoConfig` (Phase 1) provides:
- `repo_root` — path to the managed repo relative to OpsCenter root.
- `audit_types[*].command` — the command string to execute.
- `audit_types[*].working_dir` — subprocess working directory (relative to repo root).
- `audit_types[*].output_dir` — base directory where the audit bucket will appear.
- `run_id.env_var` — environment variable name for run identity injection.

Phase 6 resolves the absolute working directory by combining:
```
{OC_ROOT} / config.repo_root / at.working_dir → absolute CWD
```

---

## Relationship to Phase 3 Toolset Contract

`resolve_invocation_request()` (Phase 3) produces `ManagedAuditInvocationRequest` with:
- `command` — the executable string (split with `shlex.split`, never `shell=True`).
- `env` — environment dict including `AUDIT_RUN_ID`.
- `working_directory` — relative or absolute CWD.
- `expected_output_dir` — base directory where the audit bucket will appear.

Post-execution, Phase 3 discovery functions are called:
- `load_run_status_entrypoint(path)` → validated `ManagedRunStatus`.
- `resolve_artifact_manifest_path(run_status, base_dir=repo_root)` → absolute manifest path.

---

## Relationship to Phase 4 Run Identity

`prepare_managed_audit_invocation()` (Phase 4) generates:
- `ManagedRunIdentity` with `run_id` in the format `{repo_id}_{audit_type}_{YYYYMMDDTHHMMSSz}_{8hex}`.
- A `ManagedAuditInvocationRequest` with `AUDIT_RUN_ID={run_id}` injected into env.

The run_id is used in Phase 6 to:
1. Locate the audit bucket directory (bucket names contain the run_id string).
2. Identify the run in all downstream lifecycle records.

---

## Relationship to Phase 5 Producer Compliance

VideoFoundry (Phase 5) writes contract files to the audit bucket:
- `run_status.json` — Phase 2 schema, includes `artifact_manifest_path`.
- `artifact_manifest.json` — Phase 2 schema, lists all artifacts.

Phase 6 reads these files after process exit. The `artifact_manifest_path` field in `run_status.json` is a path relative to the VF repo root. Phase 6 resolves it using `base_dir=working_dir_abs` (the VF repo root).

---

## Dispatch Flow

```
ManagedAuditDispatchRequest
       ↓
prepare_managed_audit_invocation()        ← Phase 4+3
  → ManagedRunIdentity (run_id)
  → ManagedAuditInvocationRequest (command, env, cwd, expected_output_dir)
       ↓
_resolve_abs_working_dir()                ← Phase 6
  Resolves "." against repo_root → absolute path
       ↓
acquire_audit_lock(repo_id)               ← Phase 6
  Raises RepoLockAlreadyHeldError if already locked
       ↓
ManagedAuditExecutor.execute()            ← Phase 6
  shlex.split(command) → Popen(args, shell=False)
  stdout → {log_dir}/stdout.log
  stderr → {log_dir}/stderr.log
  timeout → SIGTERM → SIGKILL
       ↓
lock.release()  ← always in finally
       ↓
discover_post_execution()                 ← Phase 6+3
  _find_run_status_path(expected_output_dir, run_id)
    → {expected_output_dir}/{bucket_with_run_id}/run_status.json
  load_run_status_entrypoint(path)        ← Phase 3
  resolve_artifact_manifest_path(...)     ← Phase 3
       ↓
ManagedAuditDispatchResult
  status, failure_kind, process_exit_code
  run_status_path, artifact_manifest_path
  stdout_path, stderr_path, duration_seconds
```

---

## One Audit Per Repo Policy

`ManagedRepoAuditLockRegistry` maintains an in-memory set of locked `repo_id` strings.

- Acquiring a lock for a held `repo_id` raises `RepoLockAlreadyHeldError` immediately.
- The lock is released in a `finally` block after the subprocess completes (or fails/times out).
- Separate repos may be dispatched concurrently (separate lock keys).
- The module-level `_GLOBAL_REGISTRY` is process-scoped — locks do not survive process restarts.

**Crash-safety note**: If the OpsCenter process crashes while an audit is running, all held locks are dropped on restart. Process-crash-safe distributed locks are out of scope for Phase 6.

---

## Process Execution Rules

- The command string from `ManagedAuditInvocationRequest.command` is split with `shlex.split()` to produce a safe argument list.
- `subprocess.Popen` is called with `shell=False` (the default). `shell=True` is never used.
- On POSIX systems, `preexec_fn=os.setsid` creates a process group so timeout can kill the full process tree.
- Timeout escalation: SIGTERM → 10s wait → SIGKILL.

---

## Environment Propagation

Environment assembly (in dispatch priority order):
1. `request.base_env` if provided, else `os.environ.copy()`.
2. `AUDIT_RUN_ID={run_id}` injected by Phase 4's `apply_run_identity_env()`.

The subprocess receives this environment directly via `Popen(env=env)`. No shell expansion occurs — `shell=False` is enforced.

---

## Runtime Output Capture

Stdout and stderr are written to log files (not loaded into memory):

```
Default location:
  {working_dir}/tools/audit/report/dispatch/{run_id}/stdout.log
  {working_dir}/tools/audit/report/dispatch/{run_id}/stderr.log
```

Override via `dispatch_managed_audit(request, log_dir=Path(...))`.

Paths are recorded in `ManagedAuditDispatchResult.stdout_path` and `stderr_path`. The dispatch system does not parse log content.

---

## Post-Execution Discovery

Discovery is always attempted after process exit, regardless of exit code.

```
expected_output_dir/
  {bucket_containing_run_id}/
    run_status.json          ← found via targeted run_id search
      artifact_manifest_path → resolved against working_dir_abs
        → artifact_manifest.json
```

The bucket lookup scans directory names in `expected_output_dir` for those containing the `run_id` string. This is a targeted lookup by known run_id — not arbitrary artifact scanning.

Discovery failures are explicit:
- `RUN_STATUS_MISSING` — no bucket found, or no `run_status.json` in the matching bucket.
- `RUN_STATUS_INVALID` — `run_status.json` exists but fails Phase 2 contract validation.
- `MANIFEST_PATH_MISSING` — `artifact_manifest_path` field is absent or None in run_status.
- `MANIFEST_PATH_UNRESOLVABLE` — the path cannot be resolved (e.g., relative path without base).

---

## Failure Semantics

### Raise policy (programming / config errors)

| Condition | Exception |
|-----------|-----------|
| Repo not in config | `AuditDispatchConfigError` |
| audit_type not declared | `AuditDispatchConfigError` |
| Command blocked (unknown/needs_confirmation) | `AuditDispatchConfigError` |
| Concurrent dispatch for same repo | `RepoLockAlreadyHeldError` |

### Return policy (operational failures → structured result)

| Condition | `status` | `failure_kind` |
|-----------|----------|----------------|
| Process exit code != 0 | FAILED | PROCESS_NONZERO_EXIT |
| Process timed out | INTERRUPTED | PROCESS_TIMEOUT |
| Popen failed to launch | FAILED | EXECUTOR_ERROR |
| run_status.json missing | FAILED | RUN_STATUS_MISSING |
| run_status.json invalid | FAILED | RUN_STATUS_INVALID |
| artifact_manifest_path absent | FAILED | MANIFEST_PATH_MISSING |
| artifact_manifest_path unresolvable | FAILED | MANIFEST_PATH_UNRESOLVABLE |

`process_exit_code`, `run_status_path`, and `artifact_manifest_path` are populated in the result whenever available — even on failure. A nonzero exit that still produces valid contract files will have `failure_kind=PROCESS_NONZERO_EXIT` but a non-None `run_status_path`.

---

## CLI / Tool Entry Point

```
operations-center-audit run --repo videofoundry --type representative
operations-center-audit run --repo videofoundry --type representative --timeout 600
operations-center-audit run --repo videofoundry --type representative --json

operations-center-audit status <path/to/run_status.json>
operations-center-audit resolve-manifest <path/to/run_status.json> --base-dir /path/to/vf
```

Exit codes:
- `0` — dispatch completed successfully.
- `1` — dispatch failed (process or contract failure).
- `2` — repo lock already held (concurrent dispatch).
- `3` — configuration error (bad repo/type/command).

---

## Non-Goals

Phase 6 explicitly does not implement:

- **Artifact indexing** — artifacts are not enumerated or stored.
- **Directory scanning for artifacts** — the only files read are `run_status.json` and the manifest (via the contract discovery chain, not directory traversal).
- **Fixture harvesting** — no test fixtures are extracted.
- **Slice replay testing** — no replay infrastructure.
- **Persistent distributed locks** — the lock registry is in-memory and process-scoped.
- **Scheduling / watching** — no daemons or polling.
- **VideoFoundry code imports** — the hard boundary is enforced and verified by AST test.

---

## Acceptance Criteria

```
[x] OpsCenter can prepare and execute a managed audit command externally.
[x] OpsCenter enforces one audit per repo at a time.
[x] AUDIT_RUN_ID is passed to the external process.
[x] stdout/stderr are captured and referenced in the dispatch result.
[x] process exit code is recorded.
[x] run_status.json is read after process exit.
[x] artifact_manifest_path is resolved from run_status.json.
[x] nonzero process exits still attempt contract discovery.
[x] missing/invalid run_status failures are explicit.
[x] missing artifact_manifest_path failures are explicit.
[x] no artifact indexing is implemented.
[x] no directory scanning is introduced.
[x] no VideoFoundry code is imported.
[x] tests cover success, process failure, contract failure, and locking.
[x] docs explain dispatch lifecycle and non-goals.
```
