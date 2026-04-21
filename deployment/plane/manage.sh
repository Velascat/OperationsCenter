#!/usr/bin/env bash
# =============================================================================
# ControlPlane — deployment/plane/manage.sh
#
# DELEGATION WRAPPER — ControlPlane no longer owns canonical Plane infra.
#
# Plane infrastructure is managed by WorkStation/scripts/plane.sh.
# This script is kept for backward-compatibility with existing commands
# (start, plane-up, dev-up, dev-down) that call it via PLANE_MANAGER.
#
# Usage (unchanged):
#   deployment/plane/manage.sh up
#   deployment/plane/manage.sh down
#   deployment/plane/manage.sh status
#
# See WorkStation/docs/service-map.md and WorkStation/docs/operations.md
# for canonical Plane lifecycle documentation.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_PLANE_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# WorkStation is expected as a sibling of ControlPlane.
# Override with CONTROL_PLANE_WORKSTATION_DIR if the layout differs.
WORKSTATION_DIR="${CONTROL_PLANE_WORKSTATION_DIR:-$(cd "${CONTROL_PLANE_ROOT}/.." && pwd)/WorkStation}"

if [[ ! -f "${WORKSTATION_DIR}/scripts/plane.sh" ]]; then
  echo ""
  echo "  [ControlPlane] Plane infra delegation — WorkStation not found"
  echo ""
  echo "  Expected WorkStation at: ${WORKSTATION_DIR}"
  echo ""
  echo "  Plane infrastructure is canonically owned by WorkStation."
  echo "  Clone WorkStation alongside ControlPlane:"
  echo ""
  echo "    git clone https://github.com/Velascat/WorkStation ${WORKSTATION_DIR}"
  echo ""
  echo "  Or set CONTROL_PLANE_WORKSTATION_DIR to the WorkStation repo root:"
  echo ""
  echo "    export CONTROL_PLANE_WORKSTATION_DIR=/path/to/WorkStation"
  echo ""
  exit 1
fi

echo "  [ControlPlane] Delegating Plane infra to WorkStation canonical stack"
echo "  WorkStation: ${WORKSTATION_DIR}"
echo ""
exec bash "${WORKSTATION_DIR}/scripts/plane.sh" "$@"
