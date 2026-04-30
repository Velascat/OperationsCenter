# OperationsCenter End-to-End Demo

Proves the full internal boundary in one self-contained command — no Plane, no GitHub, no Kodo CLI, no Claude CLI, no network access required.

## What this demo proves

```
PlanningContext
  -> TaskProposal          (proposal_builder)
  -> LaneDecision          (stub routing, labeled as offline)
  -> ProposalDecisionBundle
  -> ExecutionCoordinator  (mandatory policy gate)
  -> DemoStubBackendAdapter
  -> ExecutionResult
  -> ExecutionRecord + ExecutionTrace  (observability recorder)
  -> retained evidence files
```

This is the complete internal path that OperationsCenter's README describes.  
After this demo, the answer to "does OperationsCenter work?" is:

> Run this command. Look at this output. Open these evidence files. That is OperationsCenter working.

## What this demo intentionally does not prove

- SwitchBoard real routing (stub routing is used and labeled as such)
- Kodo, Claude CLI, Codex CLI, Aider, or any live coding backend
- Git operations, branch push, or PR creation
- Plane board integration
- Network connectivity

For the Plane-driven golden path, see the [Autonomy-Cycle Ritual](#autonomy-cycle-ritual-full-stack) section at the bottom.

---

## Prerequisites

```bash
cd /path/to/OperationsCenter
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

No config files, environment variables, or external services needed.

---

## Demo command

```bash
python -m operations_center.entrypoints.demo.run \
    --goal "Write a tiny hello-world execution artifact" \
    --repo-key demo \
    --workspace-path /tmp/operations-center-demo \
    --backend stub
```

---

## Expected terminal output

```
============================================================
OperationsCenter Demo Run
============================================================

[PLANNING — TaskProposal]
  proposal_id : <uuid>
  task_id     : auto-simple-edit-<hash>
  task_type   : simple_edit
  risk_level  : low
  goal        : Write a tiny hello-world execution artifact

[ROUTING — LaneDecision  [stub mode — labeled, not production]]
  decision_id : <uuid>
  lane        : aider_local
  backend     : demo_stub
  rule        : demo.stub_routing
  rationale   : Offline stub routing for demo mode — deterministic, no external services required

[PROPOSAL-DECISION BUNDLE]
  <proposal_id[:8]> + <decision_id[:8]> -> bundled

[POLICY — gate result]
  status      : ALLOW
  (no violations or warnings)
  executed    : True

[EXECUTION — DemoStubBackendAdapter]
  run_id      : <uuid>
  status      : SUCCEEDED
  success     : True
  diff_stat   : 1 file changed, 6 insertions(+)
  artifact    : /tmp/operations-center-demo/artifacts/demo_result.txt

[OBSERVABILITY — retained records]
  headline    : SUCCEEDED | demo_stub @ aider_local | run=<run_id[:8]>
  summary     : Run <run_id[:8]>; changed 1 file; 1 file changed, 6 insertions(+)
  trace warn  : validation was skipped for this run
  trace warn  : no primary artifacts produced by this run

  Evidence files:
    /tmp/operations-center-demo/.operations_center/runs/<run_id>/proposal.json
    /tmp/operations-center-demo/.operations_center/runs/<run_id>/decision.json
    /tmp/operations-center-demo/.operations_center/runs/<run_id>/execution_request.json
    /tmp/operations-center-demo/.operations_center/runs/<run_id>/result.json
    /tmp/operations-center-demo/.operations_center/runs/<run_id>/execution_record.json
    /tmp/operations-center-demo/.operations_center/runs/<run_id>/execution_trace.json
    /tmp/operations-center-demo/.operations_center/runs/<run_id>/run_metadata.json

============================================================
Demo completed successfully.
============================================================
```

**Trace warnings are expected and correct:**

- `validation was skipped` — the stub adapter doesn't run validation commands
- `no primary artifacts` — the stub produces a log-excerpt artifact, not a code diff; the observability system correctly distinguishes these

Both warnings demonstrate the observability system is working, not that something is broken.

---

## Expected retained files

After the demo, inspect the evidence tree:

```
/tmp/operations-center-demo/
├── artifacts/
│   └── demo_result.txt              ← stub adapter output
└── .operations_center/
    └── runs/
        └── <run_id>/
            ├── proposal.json        ← canonical TaskProposal
            ├── decision.json        ← canonical LaneDecision
            ├── execution_request.json  ← built by ExecutionCoordinator
            ├── result.json          ← canonical ExecutionResult
            ├── execution_record.json   ← normalized observability record
            ├── execution_trace.json    ← inspectable trace report
            └── run_metadata.json    ← run summary (lane, backend, status, flags)
```

Inspect any file:

```bash
cat /tmp/operations-center-demo/.operations_center/runs/*/run_metadata.json
cat /tmp/operations-center-demo/artifacts/demo_result.txt
```

---

## Blocked-policy demo

Prove the policy gate prevents adapter invocation:

```bash
python -m operations_center.entrypoints.demo.run \
    --goal "Write a tiny hello-world execution artifact" \
    --repo-key demo \
    --workspace-path /tmp/operations-center-demo-blocked \
    --blocked-policy
```

Expected: exit code 1, no `artifacts/demo_result.txt`, evidence files retained (record + trace written even for blocked runs).

---

## Tests

```bash
# Just the demo tests
pytest tests/test_demo_stub_adapter.py tests/test_demo_routing.py tests/test_demo_cli.py -v

# Full suite (3 integration tests require live SwitchBoard — skip them if not running)
pytest --ignore=tests/integration/ -q
```

The demo test suite covers:

- `DemoStubBackendAdapter` unit contracts (artifact write, result shape, changed-file evidence)
- Stub routing produces canonical `LaneDecision`
- Demo policy gates: ALLOW path and BLOCK path
- `ExecutionCoordinator` boundary: request → policy → adapter → result → record → trace
- CLI smoke: exit 0 on success, exit 1 on blocked, all evidence files created

---

## Autonomy-Cycle Ritual (full-stack)

The self-contained demo above proves the internal boundary.  
The full-stack ritual proves the Plane integration and live backend:

```bash
# Dry-run first — inspect what would be proposed
./scripts/operations-center.sh autonomy-cycle

# If the dry-run output looks reasonable, execute
./scripts/operations-center.sh autonomy-cycle --execute
```

Confirm:
- [ ] Dry-run output shows at least one candidate or a clear suppression reason
- [ ] Artifact paths are printed and the files exist under `tools/report/operations_center/`
- [ ] `--execute` creates a Plane task with `source: autonomy` and `source: propose` labels
- [ ] The task description includes `## Proposal Provenance` with traceable run IDs

---

## Golden-Path Walkthrough (Plane + Kodo)

Full end-to-end walkthrough from local startup to a completed task with retained artifacts.

### Prerequisites

- Docker (for Plane)
- Python 3.11+
- A GitHub account with a repo and a personal access token (repo scope)
- `gh` CLI authenticated (`gh auth login`) or a `GITHUB_TOKEN` PAT
- Kodo installed and accessible via `scripts/kodo-shim`

### Step 1 — First-time setup

```bash
./scripts/operations-center.sh setup
```

This creates `.venv`, installs dependencies, and walks through the initial config wizard.

Alternatively, copy the templates manually:

```bash
cp config/operations_center.example.yaml config/operations_center.local.yaml
cp .env.operations-center.example .env.operations-center.local
```

Edit both files. Minimum required changes:

**`config/operations_center.local.yaml`**
```yaml
plane:
  project_id: <your-plane-project-uuid>

repos:
  MyRepo:
    clone_url: git@github.com:yourorg/yourrepo.git
    default_branch: main
```

**`.env.operations-center.local`**
```bash
export PLANE_API_TOKEN='your-plane-api-token'
export GITHUB_TOKEN='github_pat_...'
```

### Step 2 — Start the local stack

```bash
source .env.operations-center.local
./scripts/operations-center.sh dev-up
```

This starts:
- **Plane** on `http://localhost:8080` — Plane infra is owned by WorkStation; `dev-up` delegates to `WorkStation/scripts/plane.sh` automatically
- **Watchers**: `goal`, `test`, `improve`, `propose`, `review`

**Prerequisite:** WorkStation must be cloned as a sibling of OperationsCenter (or `OPERATIONS_CENTER_WORKSTATION_DIR` set). If WorkStation is not found, the Plane step will print instructions and exit.

Confirm everything is running:

```bash
./scripts/operations-center.sh dev-status
```

Expected output: each watcher shows `state: idle` or `state: polling`.

### Step 3 — Create a task in Plane

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

### Step 4 — Watch it execute

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

### Step 5 — Inspect retained artifacts

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

### Step 6 — Recognise success and failure signals

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

### Verification checklist

- [ ] `dev-status` shows all watchers as `idle` or `polling`
- [ ] Task transitions from `Ready for AI` → `Running` within one poll interval
- [ ] Artifacts appear in `tools/report/kodo_plane/`
- [ ] `summary.json` exists and has a recognisable outcome
- [ ] Plane task comment shows execution result details
- [ ] GitHub branch or PR exists if push succeeded

### Smoke-test shortcuts

```bash
./scripts/operations-center.sh plane-doctor --task-id <task-id>
./scripts/operations-center.sh smoke --task-id <task-id> --comment-only
```

### Cleanup

```bash
./scripts/operations-center.sh dev-down
```

### When to run the golden-path walkthrough

Run this after significant changes to the system:

- After threshold tuning (changed `min_consecutive_runs`, cooldown values, or family gates)
- After watcher restarts following a budget-exhaustion or rate-limit event
- After promoting a new candidate family from gated to active
- After any change to PR automation config (`await_review`, `bot_logins`, `max_self_review_loops`)
- Before and after a new repo is added to config
