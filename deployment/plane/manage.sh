#!/usr/bin/env bash
# =============================================================================
# OperationsCenter — deployment/plane/manage.sh
#
# DELEGATION WRAPPER — OperationsCenter no longer owns canonical Plane infra.
#
# Plane infrastructure is managed by PlatformDeployment/scripts/plane.sh.
# This script is kept for backward-compatibility with existing commands
# (start, plane-up, dev-up, dev-down) that call it via PLANE_MANAGER.
#
# Usage (unchanged):
#   deployment/plane/manage.sh up
#   deployment/plane/manage.sh down
#   deployment/plane/manage.sh status
#
# See PlatformDeployment/docs/operations/service-map.md and PlatformDeployment/docs/operations/runbook.md
# for canonical Plane lifecycle documentation.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPERATIONS_CENTER_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# PlatformDeployment is expected as a sibling of OperationsCenter.
# Override with OPERATIONS_CENTER_PLATFORM_DEPLOYMENT_DIR if the layout differs.
PLATFORM_DEPLOYMENT_DIR="${OPERATIONS_CENTER_PLATFORM_DEPLOYMENT_DIR:-$(cd "${OPERATIONS_CENTER_ROOT}/.." && pwd)/PlatformDeployment}"

if [[ ! -f "${PLATFORM_DEPLOYMENT_DIR}/scripts/plane.sh" ]]; then
  echo ""
  echo "  [OperationsCenter] Plane infra delegation — PlatformDeployment not found"
  echo ""
  echo "  Expected PlatformDeployment at: ${PLATFORM_DEPLOYMENT_DIR}"
  echo ""
  echo "  Plane infrastructure is canonically owned by PlatformDeployment."
  echo "  Clone PlatformDeployment alongside OperationsCenter:"
  echo ""
  echo "    git clone https://github.com/ProtocolWarden/PlatformDeployment ${PLATFORM_DEPLOYMENT_DIR}"
  echo ""
  echo "  Or set OPERATIONS_CENTER_PLATFORM_DEPLOYMENT_DIR to the PlatformDeployment repo root:"
  echo ""
  echo "    export OPERATIONS_CENTER_PLATFORM_DEPLOYMENT_DIR=/path/to/PlatformDeployment"
  echo ""
  exit 1
fi

echo "  [OperationsCenter] Delegating Plane infra to PlatformDeployment canonical stack"
echo "  PlatformDeployment: ${PLATFORM_DEPLOYMENT_DIR}"
echo ""
exec bash "${PLATFORM_DEPLOYMENT_DIR}/scripts/plane.sh" "$@"
