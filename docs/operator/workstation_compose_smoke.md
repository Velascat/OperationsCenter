# WorkStation compose profile smoke runbook

> Operator runbook. Closes the "WorkStation compose profile smoke per
> profile" Verification Gaps backlog item. Verification-only — surfaces
> what works, names what doesn't, does not fix. Findings worth fixing
> are filed back to backlog.
>
> Smoke run executed 2026-05-08 against current main of WorkStation +
> sibling repos. Repeat with the same commands against new images to
> re-verify.

WorkStation ships four compose profiles. This runbook brings each up,
probes its health, lists expected containers + ports, and documents
known caveats. It does **not** validate deep features — that's each
service's own concern.

## Prerequisites

- Sibling repos cloned at expected paths under `~/Documents/GitHub/`:
  `WorkStation`, `SwitchBoard`, `Archon`. (Compose contexts resolve
  relative to these locations.)
- Service images built or pullable. SwitchBoard + Archon images build
  from source; rebuild when source changes.

## Per-profile runbook

### `core` — SwitchBoard only

Minimal baseline. SwitchBoard runs alone.

```bash
cd ~/Documents/GitHub/WorkStation
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  up -d
```

| Container | Healthy? | Port | Health endpoint |
|-----------|----------|------|-----------------|
| `workstation-switchboard` | yes | `${PORT_SWITCHBOARD:-20401}` | `/health` |

Verify:

```bash
curl -fsS http://localhost:20401/health | jq .status
# → "ok"
```

**Smoke status (2026-05-08): ✅ healthy**

See `docs/operator/switchboard_live_verification.md` for the full
SwitchBoard runbook (rebuild path if you see a CxRP envelope mismatch).

### `archon` — adds Archon workflow harness

Layers on top of `core`. Archon joins as a long-running HTTP service.

```bash
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/archon.yml \
  up -d
```

| Container | Healthy? | Port | Health endpoint |
|-----------|----------|------|-----------------|
| `workstation-switchboard` | yes | `:20401` | `/health` |
| `workstation-archon` | yes | `${PORT_ARCHON:-3000}` | `/api/health` |

Verify:

```bash
curl -fsS http://localhost:3000/api/health | jq '{status, version}'
# → {"status": "ok", "version": "0.3.10", ...}
```

**Smoke status (2026-05-08): ✅ healthy**

See `docs/operator/archon_workflow_registration.md` for the codebase
registration runbook needed before `/api/workflows` returns
non-empty.

### `dev` — adds developer tooling on top of core

Adds Mailpit (local SMTP catcher) and bumps SwitchBoard to debug log
level. `mitmproxy` is commented out in the profile — uncomment if you
need traffic inspection.

```bash
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/dev.yml \
  up -d
```

| Container | Healthy? | Port(s) | Health endpoint |
|-----------|----------|---------|-----------------|
| `workstation-switchboard` | yes | `:20401` | `/health` |
| `workstation-mailpit` | yes | `:1025` (SMTP), `:8025` (web UI) | `/api/v1/info` |

Verify:

```bash
curl -fsS http://localhost:8025/api/v1/info | jq '.Name'
# → "Mailpit"
```

Mailpit's web UI is at `http://localhost:8025/`.

**Smoke status (2026-05-08): ✅ healthy**

### `observability` — Prometheus + Grafana

Layers on top of `core`. Adds Prometheus (metrics) and Grafana
(dashboards).

```bash
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/observability.yml \
  up -d
```

**Smoke status (2026-05-08): ❌ broken — config files missing**

The compose mounts (resolved):

```text
host: /home/dev/Documents/GitHub/config/observability/prometheus.yml
container: /etc/prometheus/prometheus.yml

host: /home/dev/Documents/GitHub/config/observability/grafana/provisioning
container: /etc/grafana/provisioning
```

Note: paths resolve to **sibling-of-WorkStation** under
`GitHub/config/observability/`, not under WorkStation itself. This is
intentional layout convention but undocumented.

Neither path exists in the repo today. On first start, Docker
auto-creates the host paths as empty **directories**, which then
prevents subsequent starts (Prometheus dies with
`failed to mount: not a directory: Are you trying to mount a directory
onto a file (or vice-versa)?`).

#### To unblock the observability profile

Run the one-shot script, which handles cleanup, ownership, and
skeleton-config authoring:

```bash
~/Documents/GitHub/OperationsCenter/scripts/observability-first-run.sh
```

The script:
1. Removes any auto-created stub directories (uses `sudo` once;
   prompts for password).
2. Reclaims `~/Documents/GitHub/config/observability/` for the running
   user so future writes don't need sudo.
3. Authors a minimal `prometheus.yml` + Grafana datasource
   provisioning. Idempotent — won't overwrite existing files.

After it finishes, bring the profile up:

```bash
cd ~/Documents/GitHub/WorkStation
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/observability.yml \
  up -d
```

Verify:

```bash
curl -fsS http://localhost:9090/-/healthy
# → "Prometheus Server is Healthy."
curl -fsS http://localhost:3000/api/health | jq .database
# → "ok"
```

#### Why this script exists (and why it shouldn't, eventually)

Docker bind-mounts auto-create missing host paths as root-owned
directories. When a compose file references a config file that the
operator hasn't authored yet, that "directory in place of a file"
permanently breaks subsequent starts of the container. WorkStation's
observability profile runs into this on a clean machine because the
compose file references config paths the WorkStation repo doesn't
ship.

The right long-term fix is in WorkStation: ship the skeleton config in
the repo so first-run is clean. That's filed in `.console/backlog.md`
as the WorkStation observability follow-up. This script is the bridge
until that ships.

When the `observability` and `archon` profiles are both desired
together, **port 3000 collides** — both Grafana and Archon default to
`:3000`. Override one in your local `.env` (`PORT_ARCHON=3001` or
`GRAFANA_PORT=3001`).

## Tear down

```bash
# Stop services in any active profile (the override list is forgiving):
cd ~/Documents/GitHub/WorkStation
docker compose \
  -f compose/docker-compose.yml \
  -f compose/profiles/core.yml \
  -f compose/profiles/archon.yml \
  -f compose/profiles/dev.yml \
  -f compose/profiles/observability.yml \
  stop

# Or remove containers entirely:
docker compose [...same flags...] down
```

Stopped containers retain state and volumes; bringing them back is
fast. `down` removes containers but keeps named volumes
(`prometheus_data`, `grafana_data`).

## Findings filed back to backlog

| Profile | Finding |
|---------|---------|
| `core` | None — clean. |
| `archon` | None — clean. The codebase-registration step lives in its own playbook. |
| `dev` | None — clean. The commented-out `mitmproxy` block could be removed or wired in; not urgent. |
| `observability` | **Broken on first run.** Compose references `../../config/observability/{prometheus.yml,grafana/provisioning}` (sibling-of-WorkStation paths) but those files are never authored. Docker silently creates them as empty directories, which prevents the next start. Filed as follow-up: ship a `compose/profiles/observability.config.example/` skeleton in WorkStation so first-run produces a working stack without an undocumented manual step. |
