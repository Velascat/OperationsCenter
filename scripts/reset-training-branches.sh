#!/usr/bin/env bash
# Reset all managed repos' training branch to match current origin/main.
# Run once at the start of each watchdog-loop session before invoking /loop.
#
# Repo paths are read from config/operations_center.local.yaml (gitignored)
# so no private repo names appear in this tracked file.
#
# Usage:
#   scripts/reset-training-branches.sh
#   scripts/reset-training-branches.sh --dry-run

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG="$REPO_ROOT/config/operations_center.local.yaml"
TRAINING_BRANCH="operations-center-testing-branch"
BOUNDARY_ARTIFACT="/home/dev/Documents/GitHub/PrivateManifest/dist/boundary_disclosure_artifact.json"
DRY_RUN=0

[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

if [[ ! -f "$CONFIG" ]]; then
  echo "✗ config not found: $CONFIG"
  echo "  Run: scripts/operations-center.sh setup"
  exit 1
fi

# Read repo local_paths from the gitignored config — keeps private names out of
# this tracked file. Also reads the no_verify list (repos whose main has
# pre-existing Custodian findings; mirroring is safe because we're not adding
# anything new, but the hook would block without --no-verify).
read -r -d '' PY_EXTRACT <<'PYEOF' || true
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
repos = cfg.get("repos", {})
no_verify = cfg.get("training", {}).get("no_verify_repos", [])
for key, r in repos.items():
    path = r.get("local_path", "")
    flag = "no_verify" if key in no_verify else "verify"
    if path:
        print(f"{path}\t{flag}")
PYEOF

mapfile -t REPO_LINES < <(python3 -c "$PY_EXTRACT" "$CONFIG")

if [[ ${#REPO_LINES[@]} -eq 0 ]]; then
  echo "✗ No repos found in $CONFIG"
  exit 1
fi

export REPOGRAPH_BOUNDARY_ARTIFACT_FILE="$BOUNDARY_ARTIFACT"

fail=0
for line in "${REPO_LINES[@]}"; do
  path="${line%%	*}"
  flag="${line##*	}"
  name=$(basename "$path")

  if [[ ! -d "$path/.git" ]]; then
    echo "✗ $name — not a git repo at $path"
    fail=1
    continue
  fi

  push_flags=(--force)
  [[ "$flag" == "no_verify" ]] && push_flags+=(--no-verify)

  git -C "$path" fetch origin --quiet 2>/dev/null || true

  main_sha=$(git -C "$path" rev-parse origin/main 2>/dev/null || echo "")
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

  git -C "$path" push origin \
    "origin/main:refs/heads/$TRAINING_BRANCH" \
    "${push_flags[@]}" 2>/dev/null

  tb_sha=$(git -C "$path" ls-remote origin "refs/heads/$TRAINING_BRANCH" | awk '{print $1}')
  if [[ "$main_sha" == "$tb_sha" ]]; then
    # Also advance the local branch ref so checkouts don't need another fetch.
    # Suppressed when the branch is currently checked out (shouldn't happen in
    # training mode, but safe to ignore — remote is already correct).
    git -C "$path" branch -f "$TRAINING_BRANCH" "$main_sha" 2>/dev/null || true
    echo "✓ $name"
  else
    echo "✗ $name — training branch did not match main after push"
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
