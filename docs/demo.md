# Golden-Path Demo

A single reproducible walkthrough from local startup to a completed task with retained artifacts.

## Prerequisites

- Docker (for Plane)
- Python 3.11+
- A GitHub account with a repo and a personal access token (repo scope)
- `gh` CLI authenticated (`gh auth login`) or a `GITHUB_TOKEN` PAT
- Kodo installed and accessible via `scripts/kodo-shim`

## Step 1 — First-time setup

```bash
./scripts/control-plane.sh setup
```

This creates `.venv`, installs dependencies, and walks through the initial config wizard.

Alternatively, copy the templates manually:

```bash
cp config/control_plane.example.yaml config/control_plane.local.yaml
cp .env.control-plane.example .env.control-plane.local
```

Edit both files. Minimum required changes:

**`config/control_plane.local.yaml`**
```yaml
plane:
  project_id: <your-plane-project-uuid>

repos:
  MyRepo:
    clone_url: git@github.com:yourorg/yourrepo.git
    default_branch: main
```

**`.env.control-plane.local`**
```bash
export PLANE_API_TOKEN='your-plane-api-token'
export GITHUB_TOKEN='github_pat_...'
```

## Step 2 — Start the local stack

```bash
source .env.control-plane.local
./scripts/control-plane.sh dev-up
```

This starts:
- **Plane** on `http://localhost:8080`
- **Watchers**: `goal`, `test`, `improve`, `propose`, `review`
- **Local API/UI** on `http://127.0.0.1:8787`

Confirm everything is running:

```bash
./scripts/control-plane.sh dev-status
```

Expected output: each watcher shows `state: idle` or `state: polling`.

## Step 3 — Create a task in Plane

1. Open `http://localhost:8080` and navigate to your project.
2. Create a new work item with:
   - **Title**: `Add a hello-world utility function`
   - **Labels**: `repo: MyRepo`, `task-kind: goal`
   - **Description**:
     ```
     ## Goal
     Add a small `hello_world()` function that returns the string "Hello, world!".
     Place it in `src/myrepo/utils.py` and add a corresponding test in `tests/test_utils.py`.
     ```
3. Move the work item to **`Ready for AI`** state.

## Step 4 — Watch it execute

The `goal` watcher picks up the task within its poll interval (default 30s).

Follow progress in the watcher log:

```bash
tail -f logs/local/watch-all/$(ls -t logs/local/watch-all/ | grep goal | head -1)
```

Or check the watcher status file:

```bash
cat logs/local/watch-all/goal.status.json | python3 -m json.tool
```

In Plane, the task transitions:
1. `Ready for AI` → `Running` (worker claimed the task)
2. `Running` → `In Review` (if `await_review: true` and push succeeded)
   — or `Review` (if no PR automation)
   — or `Blocked` (if validation failed)

## Step 5 — Inspect retained artifacts

Every run writes structured artifacts under `tools/report/kodo_plane/`:

```
tools/report/kodo_plane/
└── TASK-<id>/
    └── <run-id>/
        ├── request_context.json   # task + repo metadata at time of run
        ├── request.json           # full execution request
        ├── kodo_command.json      # exact kodo CLI command used
        ├── kodo_stdout.txt        # kodo stdout
        ├── kodo_stderr.txt        # kodo stderr
        ├── validation.json        # validation command results
        ├── diff_stat.txt          # git diff --stat
        ├── diff_patch.txt         # full patch
        ├── summary.json           # outcome summary (success/failure/reason)
        └── control_outcome.json   # structured outcome metadata
```

Inspect the outcome:

```bash
cat tools/report/kodo_plane/TASK-*/*/summary.json | python3 -m json.tool
```

## Step 6 — Recognise success and failure signals

**Success (branch pushed, PR created):**
- Plane task is in `In Review`
- A PR is open on GitHub pointing to `plane/<task-id>-<slug>` → `main`
- `summary.json` has `"outcome_status": "executed"` and `"success": true`

**Success (no changes needed):**
- Plane task moves to `Blocked` with outcome `no_op`
- `summary.json` has `"outcome_status": "no_op"`

**Failure (validation failed):**
- Plane task moves to `Blocked`
- `summary.json` has `"validation_passed": false`
- `validation.json` shows which command failed and its stdout/stderr
- A draft branch is still pushed (for inspection) if `push_on_validation_failure: true`

**Failure (contract violation):**
- No execution started; task moves to `Blocked` immediately
- Plane comment includes the specific failure reason (unknown repo key, missing goal, disallowed branch)

## Verification checklist

- [ ] `dev-status` shows all watchers as `idle` or `polling`
- [ ] Task transitions from `Ready for AI` → `Running` within one poll interval
- [ ] Artifacts appear in `tools/report/kodo_plane/`
- [ ] `summary.json` exists and has a recognisable outcome
- [ ] Plane task comment shows execution result details
- [ ] GitHub branch or PR exists if push succeeded

## Running the smoke test directly

To test Plane connectivity and task parsing without running a full kodo execution:

```bash
./scripts/control-plane.sh plane-doctor --task-id <task-id>
./scripts/control-plane.sh smoke --task-id <task-id> --comment-only
```

## Cleanup

```bash
./scripts/control-plane.sh dev-down
```
