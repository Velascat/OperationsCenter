#!/usr/bin/env bash
# observability-first-run.sh
# ─────────────────────────────────────────────────────────────────────────────
# One-shot script to make the WorkStation `observability` compose profile
# bootable on a clean machine.
#
# WHY:
# WorkStation/compose/profiles/observability.yml mounts host paths under
# `~/Documents/GitHub/config/observability/{prometheus.yml,grafana/...}`
# but those files are not authored anywhere in the WorkStation repo.
# When Docker can't find them, it auto-creates them as **directories**
# (root-owned), which then prevents the prometheus container from
# starting on every subsequent `up` with the cryptic error:
#   failed to mount: not a directory:
#   Are you trying to mount a directory onto a file (or vice-versa)?
#
# This script:
#   1. Removes any auto-created stubs (sudo).
#   2. Reclaims the config dir so you don't need sudo again later.
#   3. Authors a skeleton prometheus.yml + Grafana datasource provisioning.
#
# After running this, `docker compose ... -f compose/profiles/observability.yml
# up -d` boots clean.
#
# Cross-repo: the longer-term fix is for WorkStation to ship this skeleton
# in the repo so first-run is clean without this script. Tracked in
# OC `.console/backlog.md` as the WorkStation observability follow-up.

set -euo pipefail

CONFIG_DIR="${CONFIG_DIR:-$HOME/Documents/GitHub/config/observability}"
USER_NAME="${USER:-$(id -un)}"

echo "==> Target config dir: $CONFIG_DIR"
echo "==> Will reclaim ownership for: $USER_NAME"

# 1. Remove any auto-created stub directories (sudo because Docker
#    created them as root). Idempotent — safe to re-run.
if [[ -d "$CONFIG_DIR/prometheus.yml" || -d "$CONFIG_DIR/grafana" ]]; then
    echo "==> Removing auto-created stubs..."
    sudo rm -rf "$CONFIG_DIR/prometheus.yml" "$CONFIG_DIR/grafana"
fi

# 2. Make sure the config dir exists, and is owned by the running user
#    so we don't need sudo for any future writes here.
sudo mkdir -p "$CONFIG_DIR"
sudo chown -R "$USER_NAME:$USER_NAME" "$CONFIG_DIR"
echo "==> $CONFIG_DIR now owned by $USER_NAME"

# 3. Author skeleton config files (idempotent — only writes if absent
#    so an operator can hand-edit without losing their changes).
PROMETHEUS_YML="$CONFIG_DIR/prometheus.yml"
if [[ ! -f "$PROMETHEUS_YML" ]]; then
    cat > "$PROMETHEUS_YML" <<'EOF'
# Minimal Prometheus config — scrapes itself + WorkStation services as
# they expose /metrics. Authored by scripts/observability-first-run.sh;
# safe to hand-edit.
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  # Add WorkStation services as they expose /metrics. Examples:
  # - job_name: 'switchboard'
  #   static_configs:
  #     - targets: ['workstation-switchboard:20401']
EOF
    echo "==> Wrote $PROMETHEUS_YML"
else
    echo "==> $PROMETHEUS_YML already exists; leaving it alone"
fi

GRAFANA_DS_DIR="$CONFIG_DIR/grafana/provisioning/datasources"
GRAFANA_DS_FILE="$GRAFANA_DS_DIR/prometheus.yaml"
mkdir -p "$GRAFANA_DS_DIR"
if [[ ! -f "$GRAFANA_DS_FILE" ]]; then
    cat > "$GRAFANA_DS_FILE" <<'EOF'
# Grafana datasource provisioning — points the default datasource at
# the WorkStation Prometheus container. Authored by
# scripts/observability-first-run.sh; safe to hand-edit.
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://workstation-prometheus:9090
    isDefault: true
EOF
    echo "==> Wrote $GRAFANA_DS_FILE"
else
    echo "==> $GRAFANA_DS_FILE already exists; leaving it alone"
fi

echo
echo "==> Done. To bring observability up:"
echo "    cd ~/Documents/GitHub/WorkStation"
echo "    docker compose \\"
echo "        -f compose/docker-compose.yml \\"
echo "        -f compose/profiles/core.yml \\"
echo "        -f compose/profiles/observability.yml \\"
echo "        up -d"
echo
echo "==> Verify:"
echo "    curl -fsS http://localhost:9090/-/healthy   # Prometheus"
echo "    curl -fsS http://localhost:3000/api/health  # Grafana"
echo
echo "==> Note: Grafana defaults to :3000, which collides with Archon."
echo "    Don't run the archon and observability profiles simultaneously"
echo "    without overriding one of the ports in your local .env."
