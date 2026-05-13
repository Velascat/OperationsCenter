#!/usr/bin/env bash
# Reset all managed repos' training branch to match current origin/main.
# Run once at the start of each watchdog-loop session before invoking /loop.
#
# Usage:
#   scripts/reset-training-branches.sh
#   scripts/reset-training-branches.sh --dry-run

set -euo pipefail

TRAINING_BRANCH="operations-center-testing-branch"
BOUNDARY_ARTIFACT="/home/dev/Documents/GitHub/PrivateManifest/dist/boundary_disclosure_artifact.json"
DRY_RUN=0

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

# Repos whose origin/main carries known pre-existing Custodian findings.
# Mirroring main→training for these requires --no-verify (we're not introducing
# anything new; the same code already exists on the published main branch).
NO_VERIFY_REPOS=("SwitchBoard")

REPOS=(
  "/home/dev/Documents/GitHub/OperationsCenter"
  "/home/dev/Documents/GitHub/VideoFoundry"
  "/home/dev/Documents/GitHub/OperatorConsole"
  "/home/dev/Documents/GitHub/SwitchBoard"
  "/home/dev/Documents/GitHub/PlatformDeployment"
  "/home/dev/Documents/GitHub/Custodian"
  "/home/dev/Documents/GitHub/CxRP"
)

export REPOGRAPH_BOUNDARY_ARTIFACT_FILE="$BOUNDARY_ARTIFACT"

fail=0
for path in "${REPOS[@]}"; do
  name=$(basename "$path")

  if [[ ! -d "$path/.git" ]]; then
    echo "✗ $name — not a git repo at $path"
    fail=1
    continue
  fi

  # Determine whether --no-verify is needed for this repo
  push_flags=(--force)
  for nv in "${NO_VERIFY_REPOS[@]}"; do
    if [[ "$name" == "$nv" ]]; then
      push_flags+=(--no-verify)
      break
    fi
  done

  git -C "$path" fetch origin --quiet 2>/dev/null || true

  main_sha=$(git -C "$path" rev-parse origin/main 2>/dev/null)
  if [[ -z "$main_sha" ]]; then
    echo "✗ $name — could not resolve origin/main"
    fail=1
    continue
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    tb_sha=$(git -C "$path" rev-parse "origin/$TRAINING_BRANCH" 2>/dev/null || echo "MISSING")
    [[ "$main_sha" == "$tb_sha" ]] \
      && echo "= $name — already in sync" \
      || echo "~ $name — would reset ${tb_sha:0:7} → ${main_sha:0:7}"
    continue
  fi

  output=$(git -C "$path" push origin \
    "origin/main:refs/heads/$TRAINING_BRANCH" \
    "${push_flags[@]}" 2>&1)

  # Verify the push landed correctly
  tb_sha=$(git -C "$path" ls-remote origin "refs/heads/$TRAINING_BRANCH" | awk '{print $1}')
  if [[ "$main_sha" == "$tb_sha" ]]; then
    echo "✓ $name"
  else
    echo "✗ $name — push output: $output"
    fail=1
  fi
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo ""
  echo "Dry run complete. Re-run without --dry-run to apply."
  exit 0
fi

echo ""
if [[ "$fail" -eq 0 ]]; then
  echo "All training branches reset to origin/main."
else
  echo "One or more resets failed — see output above."
  exit 1
fi
