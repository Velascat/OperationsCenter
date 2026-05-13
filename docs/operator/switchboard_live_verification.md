# SwitchBoard live verification runbook

> Operator runbook. Closes the "SwitchBoard live verification rev"
> Verification Gaps backlog item. Companion to
> `docs/operator/archon_workflow_registration.md` — same verify-only
> shape: bring service up, prove the integration path, document the
> failure modes that surfaced.

OC's in-process SwitchBoard tests (`tests/unit/routing/`) prove the
schema, the cxrp_mapper round-trip, and the in-memory client. What was
never exercised before this rev: an HTTP service actually serving
`/route` against OC's `HttpLaneRoutingClient`. This runbook gets you
there in five steps.

## 0. Prerequisites

- The SwitchBoard image is built. If `docker images | grep switchboard`
  shows nothing or a stale entry, rebuild (step 2 below).
- The PlatformDeployment `compose/profiles/core.yml` profile is wired (it is
  by default — SwitchBoard is the only service in the core profile).

## 1. Bring up the SwitchBoard service

```bash
cd ~/Documents/GitHub/PlatformDeployment
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  up -d switchboard
```

Verify health:

```bash
curl -fsS http://localhost:20401/health | jq .
# → {"status":"ok","version":"0.1.0",...,"selector_ready":true,"policy_valid":true,"policy_issues":[]}
```

The default port is `${PORT_SWITCHBOARD:-20401}`. If you override it,
also point `routing.base_url` in your OC settings at the new value.

## 2. Verify the image is current

The CxRP envelope flip went into SwitchBoard's `routes_routing.py` mid-
2026 — `/route` now emits a CxRP-shaped response (`contract_kind:
"lane_decision"`, `schema_version: "0.x"`). OC's
`routing.client._decode_route_response` no longer accepts the older OC-
shaped response.

If your container was built before this flip, every routing call from
OC raises:

```
ValueError: Unexpected /route response shape: expected CxRP LaneDecision
envelope (contract_kind='lane_decision', schema_version='0.x'). Got
contract_kind=None, schema_version=None.
```

**Fix**: rebuild the image so it picks up current source:

```bash
cd ~/Documents/GitHub/PlatformDeployment
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  build switchboard

docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  up -d --force-recreate switchboard
```

Confirm the wire shape:

```bash
curl -s -X POST http://localhost:20401/route -H 'Content-Type: application/json' -d '{
  "task_id": "smoke-1",
  "project_id": "smoke",
  "task_type": "lint_fix",
  "execution_mode": "goal",
  "goal_text": "smoke test",
  "target": {"repo_key": "demo", "clone_url": "https://example.invalid/demo.git", "base_branch": "main"}
}' | jq '{contract_kind, schema_version, lane, executor, backend}'
```

Expected:

```json
{
  "contract_kind": "lane_decision",
  "schema_version": "0.3",
  "lane": "coding_agent",
  "executor": "codex_cli",
  "backend": "kodo"
}
```

## 3. Run the live integration suite

```bash
cd ~/Documents/GitHub/OperationsCenter
source .venv/bin/activate
python -m pytest tests/integration/test_routing_live.py -v
```

Expected: **4 pass / 0 fail.** All four test:

- `test_canonical_proposal_returns_lane_decision` — POST /route round-trip
- `test_response_validates_as_canonical_lane_decision` — decoder accepts envelope
- `test_different_proposals_may_receive_decisions` — different inputs may select different lanes
- `test_unreachable_switchboard_raises_unavailable_error` — graceful failure when service is down

If any fail, see step 2 (image staleness) or step 5 (failure modes).

## 4. Tear down

```bash
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  stop switchboard
```

The container record persists across `stop`/`start` so you can bring
it back without rebuilding. Use `down` to remove the container and
reset.

## 5. Failure-mode crib sheet

These are the four real errors observed during this rev's
investigation. Same shape as the Archon playbook — discovered by
hitting the service, not by reading the code.

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ValueError: Unexpected /route response shape: expected CxRP LaneDecision envelope` | Image was built before the CxRP wire flip; container ships an older `/route` returning OC's rich `LaneDecision` shape directly. | Step 2 — rebuild + force-recreate. |
| `{"detail":[{"type":"missing","loc":["body","task_id"],...}]}` | Posted body uses OC's older `proposal_id`/`task_type`-only shape; SwitchBoard's `TaskProposal` schema requires `task_id`, `project_id`, `execution_mode`, `target`. | Use the full TaskProposal shape (see step 2 example). OC's `HttpLaneRoutingClient` handles this automatically — ad-hoc curl probes do not. |
| `{"detail":[{"type":"enum","loc":["body","execution_mode"],"msg":"Input should be 'goal', 'fix_pr', 'test_campaign' or 'improve_campaign'"}]}` | Used a free-form `execution_mode` string. The accepted enum is narrow. | Use `goal`, `fix_pr`, `test_campaign`, or `improve_campaign`. |
| `SwitchBoardUnavailableError` from `HttpLaneRoutingClient` | Service not running or wrong base_url. | `docker ps` to confirm the container is up; check `routing.base_url` in OC settings matches the host port. The unreachable case is covered by `test_unreachable_switchboard_raises_unavailable_error` and is the one failure mode that does not require the service to be running. |

## 6. Where this fits in OC settings

```yaml
# config/operations-center.yaml (operator-owned slice)
routing:
  enabled: true
  base_url: http://localhost:20401
  timeout_s: 5.0
```

When `routing.enabled` is true, OC's planning step calls
`HttpLaneRoutingClient.select_lane(proposal)` against this URL. The
returned `LaneDecision` flows through the rest of the dispatch path
unchanged.

## What this rev does not validate

- **A live happy-path dispatch under SwitchBoard's decision.** The
  routing tests prove `/route` round-trips correctly. Whether the
  *backend* SwitchBoard selected (kodo / archon / direct_local) then
  runs successfully is a separate concern — it depends on that
  backend's own infrastructure (LLM credentials, container health,
  etc.) and is covered by Archon Rev 3 + ADR 0003 follow-ups.
- **Multi-tenant routing.** SwitchBoard's selector is single-policy
  in this image; multi-policy / per-project policy routing is a
  future concern, not in this image's contract.
