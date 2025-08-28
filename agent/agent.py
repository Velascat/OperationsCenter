"""
Long-running Codex agent.

- Periodically:
  1) sync repo -> feature branch
  2) generate plan via 'strands' + warehouse
  3) apply plan via Codex CLI
  4) commit + push; optional PR out-of-band

Style notes:
- 89-col line width.
- Multi-line args with trailing commas.
- Multi-line docstrings with newline padding.
"""

from __future__ import annotations

import json
import os
import random
import shlex
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_DIR = Path(os.environ.get("REPO_DIR", "/workspace"))
STATE_DIR = Path(os.environ.get("STATE_DIR", "/workspace/.codex"))
BRANCH_BASE = os.environ.get("BRANCH_BASE", "codex/update")
MAIN_BRANCH = os.environ.get("MAIN_BRANCH", "main")
RUN_MIN_INTERVAL_S = int(os.environ.get("RUN_MIN_INTERVAL_S", "900"))  # 15 min
RUN_JITTER_S = int(os.environ.get("RUN_JITTER_S", "120"))              # +/- 2 min
LOCK_FILE = STATE_DIR / "agent.lock"
LAST_RUN_FILE = STATE_DIR / "last_run.json"

CODEX_API_KEY = os.environ.get("CODEX_API_KEY")
GIT_AUTHOR_NAME = os.environ.get("GIT_AUTHOR_NAME", "codex-bot")
GIT_AUTHOR_EMAIL = os.environ.get("GIT_AUTHOR_EMAIL", "bot@example.com")

_shutdown = False


def log(event: str, **fields: object) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    payload = {"ts": ts, "event": event, **fields}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def run(
    cmd: list[str],
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    allow_fail: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a shell command with logging and optional failure tolerance.
    """
    text = " ".join(shlex.quote(x) for x in cmd)
    log("exec.start", cmd=text, cwd=str(cwd or Path.cwd()))
    cp = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    log(
        "exec.end",
        cmd=text,
        returncode=cp.returncode,
        stdout=cp.stdout[-5000:],
        stderr=cp.stderr[-5000:],
    )
    if cp.returncode != 0 and not allow_fail:
        raise RuntimeError(f"Command failed: {text}")
    return cp


def ensure_git_identity() -> None:
    run(["git", "config", "user.name", GIT_AUTHOR_NAME], cwd=REPO_DIR)
    run(["git", "config", "user.email", GIT_AUTHOR_EMAIL], cwd=REPO_DIR)


def sync_repo(feature_branch: str) -> None:
    """
    Bring repo up to date and land on a safe feature branch.
    """
    run(["git", "fetch", "origin", "--prune"], cwd=REPO_DIR)
    # Ensure main exists locally, fast-forward it.
    run(["git", "checkout", MAIN_BRANCH], cwd=REPO_DIR, allow_fail=True)
    run(["git", "reset", "--hard", f"origin/{MAIN_BRANCH}"], cwd=REPO_DIR)
    # Create/switch feature branch off main.
    run(["git", "checkout", "-B", feature_branch], cwd=REPO_DIR)
    # Make sure feature has main's latest.
    run(["git", "merge", "--ff-only", MAIN_BRANCH], cwd=REPO_DIR, allow_fail=True)


def generate_plan() -> Path:
    """
    Use strands + warehouse to emit a plan file for Codex.
    Replace the commands here with your real pipeline invocations.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = STATE_DIR / "plan.json"

    # Example: strands discovers the graph, warehouse composes a plan.
    # Replace these with your real entrypoints.
    run(
        [
            "python",
            "-m",
            "strands.cli",
            "discover",
            "--repo",
            str(REPO_DIR),
            "--out",
            str(STATE_DIR / "graph.json"),
        ],
        cwd=REPO_DIR,
        allow_fail=False,
    )
    run(
        [
            "python",
            "-m",
            "warehouse.plan",
            "--graph",
            str(STATE_DIR / "graph.json"),
            "--out",
            str(plan_path),
        ],
        cwd=REPO_DIR,
        allow_fail=False,
    )
    return plan_path


def apply_plan(plan_path: Path) -> bool:
    """
    Apply Codex plan. Returns True if files changed.
    """
    env = os.environ.copy()
    if CODEX_API_KEY:
        env["CODEX_API_KEY"] = CODEX_API_KEY

    run(
        [
            "codex",
            "apply",
            "--repo",
            str(REPO_DIR),
            "--plan",
            str(plan_path),
        ],
        cwd=REPO_DIR,
        env=env,
        allow_fail=False,
    )

    # stage any changes to detect diffs
    run(["git", "add", "-A"], cwd=REPO_DIR)
    diffcheck = run(["git", "diff", "--cached", "--quiet"], cwd=REPO_DIR)
    # returncode 0 => no diff; 1 => diff present
    changed = diffcheck.returncode == 1
    return changed


def commit_and_push(feature_branch: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # commit if needed
    run(
        [
            "git",
            "-c",
            f"user.name={GIT_AUTHOR_NAME}",
            "-c",
            f"user.email={GIT_AUTHOR_EMAIL}",
            "commit",
            "-m",
            f"Codex: apply plan ({ts})",
        ],
        cwd=REPO_DIR,
        allow_fail=True,
    )
    # push
    run(["git", "push", "-u", "origin", feature_branch], cwd=REPO_DIR, allow_fail=True)


def rate_limit_ok() -> bool:
    if not LAST_RUN_FILE.exists():
        return True
    try:
        meta = json.loads(LAST_RUN_FILE.read_text())
        last = int(meta.get("epoch_s", 0))
        now = int(time.time())
        return (now - last) >= RUN_MIN_INTERVAL_S
    except Exception:
        return True


def write_last_run() -> None:
    LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_RUN_FILE.write_text(json.dumps({"epoch_s": int(time.time())}))


def acquire_lock() -> bool:
    if LOCK_FILE.exists():
        return False
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)  # py3.11
    except Exception:
        pass


def handle_sigterm(_signum, _frame) -> None:
    global _shutdown
    _shutdown = True
    log("signal", msg="shutdown requested")


def main() -> int:
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    ensure_git_identity()
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    while not _shutdown:
        try:
            if not acquire_lock():
                log("lock.busy", note="another run in progress")
            elif rate_limit_ok():
                feature_branch = f"{BRANCH_BASE}/{datetime.now().strftime('%Y%m%d')}"
                log("run.begin", feature_branch=feature_branch)
                sync_repo(feature_branch=feature_branch)
                plan = generate_plan()
                changed = apply_plan(plan_path=plan)
                if changed:
                    commit_and_push(feature_branch=feature_branch)
                    log("run.commit", changed=True)
                else:
                    log("run.commit", changed=False)
                write_last_run()
                log("run.end", ok=True)
            else:
                log("rate.limit", cooldown_s=RUN_MIN_INTERVAL_S)

        except Exception as e:
            log("run.error", error=str(e))
        finally:
            release_lock()

        # sleep with small jitter
        nap = RUN_MIN_INTERVAL_S + random.randint(-RUN_JITTER_S, RUN_JITTER_S)
        nap = max(60, nap)
        for _ in range(nap):
            if _shutdown:
                break
            time.sleep(1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
