# SwitchBoard Configuration

How OperationsCenter locates SwitchBoard and what happens when it isn't available.

---

## Summary

OperationsCenter delegates all lane selection to SwitchBoard via HTTP.

```text
OperationsCenter builds TaskProposal
    ↓  POST /route
SwitchBoard evaluates policy
    ↓  LaneDecision (JSON)
OperationsCenter validates and stores the decision
```

OperationsCenter never chooses a lane itself. If SwitchBoard is unreachable, the
request fails with a clear error.

---

## Endpoint

| Property | Value |
|---|---|
| Default URL | `http://localhost:20401` |
| Route endpoint | `POST /route` |
| Health endpoint | `GET /health` |
| Request body | `TaskProposal` (Pydantic v2, JSON) |
| Response body | `LaneDecision` (Pydantic v2, JSON) |

SwitchBoard listens on port **20401** by default. This is set in PlatformDeployment's
`docker-compose.yml`.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPERATIONS_CENTER_SWITCHBOARD_URL` | `http://localhost:20401` | Full base URL for the SwitchBoard service |

Set this in your shell or in PlatformDeployment's `.env` file:

```bash
OPERATIONS_CENTER_SWITCHBOARD_URL=http://localhost:20401
```

To point at a remote SwitchBoard instance:

```bash
OPERATIONS_CENTER_SWITCHBOARD_URL=http://switchboard.internal:20401
```

---

## How OperationsCenter Reads the URL

`HttpLaneRoutingClient.from_env()` reads `OPERATIONS_CENTER_SWITCHBOARD_URL` and
falls back to the default if the variable is not set:

```python
from operations_center.routing import HttpLaneRoutingClient

client = HttpLaneRoutingClient.from_env()
```

`PlanningService.default()` uses `HttpLaneRoutingClient.from_env()` internally:

```python
from operations_center.routing import PlanningService

service = PlanningService.default()
bundle = service.plan(context)
# bundle.decision  → LaneDecision from SwitchBoard
```

---

## Failure Behavior

If SwitchBoard is unreachable or times out, `HttpLaneRoutingClient.select_lane()`
raises `SwitchBoardUnavailableError` with an actionable message:

```
SwitchBoardUnavailableError: SwitchBoard unreachable at http://localhost:20401.
Set OPERATIONS_CENTER_SWITCHBOARD_URL or start the SwitchBoard service.
Cause: <original httpx error>
```

OperationsCenter does **not** fall back to a default lane. The caller must handle
or propagate the error.

```python
from operations_center.routing import SwitchBoardUnavailableError

try:
    bundle = service.plan(context)
except SwitchBoardUnavailableError as exc:
    # SwitchBoard is down — fail the task clearly
    print(f"[ERROR] {exc}")
    raise
```

If SwitchBoard returns a 4xx or 5xx response, `httpx.HTTPStatusError` is raised
(not wrapped), so the HTTP status code is visible in the exception.

---

## Verifying the Connection

```bash
# Check SwitchBoard health directly
curl http://localhost:20401/health

# Run the integration tests (stack must be running)
pytest tests/integration/ -v
```

The integration tests in `tests/integration/test_routing_live.py` auto-skip
if SwitchBoard is not reachable, so they are safe to include in full test runs.

---

## Startup Dependency

SwitchBoard must be running before OperationsCenter can route any proposal.

PlatformDeployment's `scripts/up.sh` starts SwitchBoard and waits for it to become
healthy before returning. If you start OperationsCenter independently, ensure
SwitchBoard is healthy first:

```bash
curl -f http://localhost:20401/health || echo "SwitchBoard not ready"
```
