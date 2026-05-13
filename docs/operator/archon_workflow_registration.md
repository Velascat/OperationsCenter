# Archon workflow registration playbook

> Operator runbook. No OC code changes are required to follow this playbook.
> Closes the "what the live container could not validate" line in
> `.console/validation/post_extraction_runtime_2026-05-08-rev3.md`.

When OC dispatches to the `archon` backend, it routes via
`workflow_type` → workflow name (default mapping in
`AiderSettings.archon.workflow_names`, e.g. `goal` → `archon-assist`).
For a workflow_run to actually start server-side, the running Archon
container must be able to:

1. Find a workflow definition with that name.
2. Resolve a working directory that belongs to a *registered codebase*.

Both are environment setup, not OC concerns. This playbook walks
through the four steps an operator runs once per Archon deployment.

## 0. Prerequisites

- The Archon image is built and present locally (`docker images | grep archon`).
- The PlatformDeployment `compose/profiles/archon.yml` profile is wired (it is
  by default — see `PlatformDeployment/compose/profiles/archon.yml`).
- The OperationsCenter codebase you want Archon to act on is
  reachable as a git URL or already mounted into the container.

## 1. Bring up the Archon service

```bash
cd ~/Documents/GitHub/PlatformDeployment
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/archon.yml \
  up -d archon
```

Verify health:

```bash
curl -fsS http://localhost:3000/api/health | jq .status
# → "ok"
```

The default port is `${PORT_ARCHON:-3000}`. If you override `PORT_ARCHON`
in `.env`, also set `archon.base_url` in your OC settings.

## 2. Verify the workflow ships with the image

The Archon image bundles ~20 default workflows, including the
`archon-assist` workflow that OC's `goal` workflow_type resolves to.

```bash
docker exec workstation-archon ls /app/.archon/workflows/defaults/ | grep archon-assist
# → archon-assist.yaml
```

Skip ahead to step 3 — the workflow file is on disk; what's missing is a
codebase scope to make it visible via the API.

## 3. Register a codebase

Without a registered codebase, `GET /api/workflows` returns
`{"workflows":[]}` because Archon refuses to resolve workflows in an
unscoped `cwd`. Register the OC codebase by URL (Archon clones it to
`/.archon/workspaces/`) or by an in-container path that's a git repo:

```bash
# Option A — clone from a git URL (simplest; recommended)
curl -fsS -X POST http://localhost:3000/api/codebases \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://github.com/<your-org>/<your-repo>.git"}' | jq .

# Option B — register an existing in-container path that is a git repo
# (only works if you mounted it via the compose volume:)
curl -fsS -X POST http://localhost:3000/api/codebases \
  -H 'Content-Type: application/json' \
  -d '{"path":"/path/inside/container"}' | jq .
```

Capture the `default_cwd` field from the response — that's the path OC's
archon dispatch will pass on each `/api/workflows/{name}/run` call.

## 4. Verify the workflow is now visible

```bash
CWD=$(curl -fsS http://localhost:3000/api/codebases | jq -r '.[0].default_cwd')
curl -fsS "http://localhost:3000/api/workflows?cwd=$CWD" | jq '.workflows[].workflow.name' | grep archon-assist
# → "archon-assist"
```

If `archon-assist` shows up in the list, OC's `goal` workflow_type can
resolve.

## 5. Drive a real OC dispatch

With the codebase registered and an LLM credential present in the
container (Anthropic / OpenAI key — Archon's responsibility, see Archon
docs), an OC archon dispatch will actually run end-to-end. From the OC
repo:

```bash
source .venv/bin/activate
python -m operations_center.entrypoints.archon_probe.main
# → [OK] archon healthy (HTTP 200)
```

Then run an OC execute against your bundle of choice with `archon`
configured as a backend in your settings. The run's
`runtime_invocation_ref` will land in `execution_trace.json` with
`runtime_kind=http_async`; `routing.selected_backend=archon`; and a
non-empty `failure_reason` only if Archon itself failed.

## 6. Tear down

```bash
docker compose -f compose/docker-compose.yml -f compose/profiles/core.yml -f compose/profiles/archon.yml stop archon
```

The container record (and any registered codebases) persists across
`stop`/`start`. Use `down` to remove the container and reset.

## Failure-mode crib sheet

| Symptom | Cause | Fix |
|---------|-------|-----|
| `GET /api/workflows` returns `{"workflows":[]}` | No codebase registered, or query has no `cwd` | Step 3 + pass `cwd=` matching `default_cwd` |
| `{"error":"Invalid cwd: must match a registered codebase path"}` | `cwd` doesn't match any registered codebase | Use the exact `default_cwd` from `GET /api/codebases` |
| `{"error":"Failed to add codebase: Path is not a git repository: …"}` | Path supplied isn't a git repo *inside the container* | Use the URL form (Option A) or mount a real git checkout |
| OC archon run kicks off but `by-worker` 404s briefly | Archon's pre-registration window | Already handled — OC's `AsyncHttpRunner` tolerates this and falls through to poll |
| OC archon run reports `outcome=timeout` | Workflow accepted but the upstream LLM never replied (e.g. missing API key) | Provision the LLM credential per Archon docs; raise OC's `archon.poll_interval_seconds` / per-request timeout if needed |

## Where this fits in OC settings

```yaml
# config/operations-center.yaml (operator-owned slice)
archon:
  enabled: true
  base_url: http://localhost:3000
  poll_interval_seconds: 2.0
  workflow_names:
    goal:    archon-assist           # ← step 4 verified this is registered
    fix_pr:  archon-fix-github-issue
    test:    archon-test-loop-dag
    improve: archon-refactor-safely
```

If you ship a custom workflow under `~/.archon/workflows/` or a
project-scoped `.archon/workflows/` inside the registered codebase,
add the mapping here and re-run step 4 to confirm the new name appears.
