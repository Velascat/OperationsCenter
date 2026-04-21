# Phase 5 — SwitchBoard Integration

Phase 5 wires Control Plane to SwitchBoard as its model access layer.
All LLM calls from Control Plane now flow through SwitchBoard, making
routing decisions visible and policy-controlled.

```
Control Plane → SwitchBoard → 9router → Provider → Response
```

---

## What Changed

| Area | Before | After |
|------|--------|-------|
| LLM calls | Direct Claude CLI subprocess | HTTP to SwitchBoard `/v1/chat/completions` |
| Model selection | Hard-coded model name | SwitchBoard policy (forced via `X-SwitchBoard-Profile`) |
| Observability | None | All calls appear in SwitchBoard decision log with tenant ID |
| Correlation | None | `X-Request-ID` on every call — inspectable via admin API |
| Fallback | N/A | Falls back to Claude CLI if `SWITCHBOARD_URL` not set |

---

## Configuration

### Option A — environment variable (recommended)

```bash
export SWITCHBOARD_URL=http://localhost:20401
```

Set this before starting any Control Plane worker or entrypoint.
All `call_claude()` calls will route through SwitchBoard automatically.

### Option B — `control_plane.yaml`

```yaml
spec_director:
  switchboard_url: http://localhost:20401
```

The entrypoint reads this and sets `SWITCHBOARD_URL` before running.
The environment variable takes precedence if both are set.

### No configuration

When `SWITCHBOARD_URL` is absent (and no `spec_director.switchboard_url`),
all LLM calls fall back to the Claude CLI subprocess.
This preserves the pre-Phase-5 behaviour and requires no migration.

---

## How Routing Works

Control Plane sets `X-SwitchBoard-Profile` explicitly based on the
model requested:

| Control Plane model | SwitchBoard profile |
|---------------------|---------------------|
| `claude-opus-4-*`   | `capable`           |
| `claude-sonnet-4-*` | `capable`           |
| `claude-haiku-4-*`  | `fast`              |
| unknown             | `capable` (default) |

Both the `BrainstormService` (brainstorm model, default `claude-opus-4-6`)
and `SpecComplianceService` (compliance model, default `claude-sonnet-4-6`)
route to the `capable` profile.

All requests are tagged with `X-SwitchBoard-Tenant-ID: control-plane`
for filtering in the decision log.

---

## Observability

Every LLM call from Control Plane produces a decision record in SwitchBoard
with:

- `tenant_id: control-plane`
- `X-Request-ID`: a unique hex ID per call

### Verify a Control Plane request reached SwitchBoard

```bash
# See recent decisions tagged control-plane
python scripts/inspect.py recent 20
# Look for entries with profile: capable, task_type: planning/code

# Aggregated view
python scripts/inspect.py summary 100
# Check profile_counts.capable for Control Plane traffic
```

---

## Example Task Execution

The `BrainstormService` is the first Control Plane component that now
runs through SwitchBoard.  To trigger it manually:

```bash
# With SwitchBoard running
export SWITCHBOARD_URL=http://localhost:20401

python -m control_plane.entrypoints.spec_director.main \
    --config config/control_plane.local.yaml \
    --once
```

Expected log output includes a `spec_brainstorm` event.  The corresponding
SwitchBoard decision will appear in `GET /admin/decisions/recent` with:

```json
{
  "tenant_id": "control-plane",
  "profile_name": "capable",
  "rule_name": "...",
  "task_type": "planning"
}
```

---

## Where LLM Calls Are Made

| Component | Service | Default model | SwitchBoard profile |
|-----------|---------|---------------|---------------------|
| spec_director | `BrainstormService` | `claude-opus-4-6` | `capable` |
| spec_director | `SpecComplianceService` | `claude-sonnet-4-6` | `capable` |
| spec_director | `PhaseOrchestrator` | `claude-sonnet-4-6` | `capable` |

All three go through `call_claude()` in `spec_director/_claude_cli.py`.

---

## Error Handling

When SwitchBoard is unavailable:

- `SwitchBoardError` is raised from `SwitchBoardClient.complete()`
- This propagates as a `BrainstormError` / `api_failure` in the compliance service
- The spec_director entrypoint logs a structured error and skips the cycle

No automatic retries in Phase 5 — operators should check SwitchBoard health.

---

## Separation of Concerns

| Layer | Responsibility |
|-------|---------------|
| Control Plane | **Decides what task to run** (brainstorm, compliance check) |
| SwitchBoard | **Decides which model to use** (policy engine + eligibility) |
| 9router | **Executes the transport** to the provider |

Control Plane never selects a model directly — only a *profile intent*.
SwitchBoard may override even that via policy rules.

---

## Current Limitations

- **spec_director only.** Only the `spec_director` subsystem routes through
  SwitchBoard.  Kodo task execution is out of scope for Phase 5.
- **No streaming.** All `call_claude()` calls are synchronous non-streaming.
- **Single worker.** No retry logic; transient SwitchBoard errors will fail
  the current spec cycle and retry on the next poll interval.
