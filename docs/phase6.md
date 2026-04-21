# Phase 6 — Multi-Executor Layer

Phase 6 introduces a clean executor boundary so Control Plane can dispatch
tasks to different execution engines without changing model routing logic.

```
Control Plane → ExecutorFactory → AiderAdapter  ─┐
                                 KodoExecutorAdapter ─┤→ SwitchBoard → 9router → Provider
```

---

## What Changed

| Area | Before | After |
|------|--------|-------|
| Execution | KodoAdapter hard-wired into ExecutionService | Any Executor implementation via factory |
| Abstraction | None | `Executor` protocol + `ExecutorTask` / `ExecutorResult` |
| Executors | Kodo only | Kodo + Aider |
| Selection | Always Kodo | Per-repo `executor:` field in config |
| SwitchBoard routing | Aider: N/A; Kodo: none | Aider: full (OPENAI_API_BASE); Kodo: workers (OPENAI_API_BASE) |

---

## Executor Interface

```python
# control_plane/adapters/executor/protocol.py

@dataclass
class ExecutorTask:
    goal: str
    repo_path: Path
    constraints: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class ExecutorResult:
    success: bool
    output: str
    exit_code: int | None = None
    executor: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

class Executor(Protocol):
    def execute(self, task: ExecutorTask) -> ExecutorResult: ...
    def name(self) -> str: ...
```

---

## Executors

### AiderAdapter

Routes **all** model calls through SwitchBoard via `OPENAI_API_BASE`.

- Invokes Aider as a subprocess with `--message <goal> --yes`
- `OPENAI_API_BASE=<switchboard_url>/v1` so every Aider model call goes through SwitchBoard
- `--model openai/<profile>` — SwitchBoard profile resolved from `AiderSettings.profile`
  or overridden per-task via `task.metadata["profile"]`
- Captures stdout/stderr and returns normalized `ExecutorResult`

**Configuration** (`config/control_plane.yaml`):

```yaml
aider:
  binary: /home/dev/Documents/GitHub/SwitchBoard/.venv-aider/bin/aider
  profile: capable               # default SwitchBoard profile
  timeout_seconds: 3600
  model_settings_file: /home/dev/Documents/GitHub/SwitchBoard/config/aider/model-settings.yml
```

### KodoExecutorAdapter

Wraps the existing `KodoAdapter` behind the `Executor` interface.

- Converts `ExecutorTask` → Kodo `write_goal_file()` + `run()`
- Injects `OPENAI_API_BASE=<switchboard_url>/v1` into the Kodo subprocess environment
  so any OpenAI-compatible worker agents (team `full` codex workers) route through SwitchBoard
- Converts `KodoRunResult` → `ExecutorResult`
- Supports `task.metadata["kodo_mode"]` (`"goal"` | `"test"` | `"improve"`)

**SwitchBoard routing note:** Kodo's *orchestrator* (Claude Code CLI) does not use
`OPENAI_API_BASE` and does not route through SwitchBoard.  Only worker agents
using the OpenAI backend are affected.

---

## Executor Selection

Selection is deterministic and config-driven.

### Per-repo field

```yaml
# config/control_plane.yaml
repos:
  SwitchBoard:
    executor: aider   # this repo uses AiderAdapter
  VideoFoundry:
    executor: kodo    # this repo uses KodoExecutorAdapter (default)
```

Default: `"kodo"` — backward compatible with all existing repos.

### Factory API

```python
from control_plane.adapters.executor.factory import ExecutorFactory

# Create by type
executor = ExecutorFactory.create("aider", settings)

# Create from repo config
executor = ExecutorFactory.for_repo("SwitchBoard", settings)

# Run a task
result = executor.execute(ExecutorTask(
    goal="Add unit tests for the classifier",
    repo_path=Path("/checkout/SwitchBoard"),
))
```

---

## SwitchBoard Consistency

Both adapters accept a `switchboard_url` wired by `ExecutorFactory`:

1. `SWITCHBOARD_URL` env var (takes precedence)
2. `spec_director.switchboard_url` in YAML config

```bash
export SWITCHBOARD_URL=http://localhost:20401
```

To verify routing decisions appear in SwitchBoard:

```bash
# After running a task with AiderAdapter:
python scripts/inspect.py recent 10
# Look for entries with tenant_id: control-plane, profile: capable
```

---

## How to Add a New Executor

1. Create `src/control_plane/adapters/executor/<name>.py`
2. Implement `execute(task: ExecutorTask) -> ExecutorResult` and `name() -> str`
3. Set `OPENAI_API_BASE` from the `switchboard_url` argument (passed by factory)
4. Add a branch in `ExecutorFactory.create()` for the new type name
5. Add `executor: <name>` to repos in config as needed
6. Write tests

---

## Example Task Execution

```python
from pathlib import Path
from control_plane.adapters.executor.factory import ExecutorFactory
from control_plane.adapters.executor.protocol import ExecutorTask

settings = load_settings("config/control_plane.local.yaml")

# Aider task
executor = ExecutorFactory.create("aider", settings)
result = executor.execute(ExecutorTask(
    goal="Write a docstring for every public function in src/classifier.py",
    repo_path=Path("/checkout/SwitchBoard"),
    constraints="Only modify src/switchboard/services/classifier.py",
))
print(result.success, result.output[:200])

# Kodo task
executor = ExecutorFactory.create("kodo", settings)
result = executor.execute(ExecutorTask(
    goal="Add a pytest fixture for the database connection",
    repo_path=Path("/checkout/MyRepo"),
    metadata={"kodo_mode": "test"},
))
print(result.success, result.metadata["command"])
```

---

## Current Limitations

- **`ExecutionService` not migrated.** The existing `ExecutionService` still
  uses `KodoAdapter` directly.  Phase 6 adds the layer alongside it — migration
  is a separate step that touches a large, well-tested service.
- **Kodo orchestrator not routed.** Kodo's Claude Code CLI orchestrator does not
  use `OPENAI_API_BASE`; only OpenAI-compatible worker agents are affected by
  the SwitchBoard env var injection.
- **Aider one-shot only.** `AiderAdapter` uses `--message` for non-interactive
  single-task execution.  Multi-turn interactive sessions are not supported.
