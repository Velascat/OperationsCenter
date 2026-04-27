"""Intake role — operator queue processor.

Watches ~/.console/queue/ for task files submitted by `console run`.
For each queued task:
  1. Loads context from the repo's .console/ files and recent git commits
  2. Elaborates the raw goal into a well-scoped PlanningContext
  3. Drives the full planning → execution pipeline
  4. Deletes the queue file on success (leaves it on failure for inspection)

Queue directory: ~/.console/queue/<uuid>.json

Each queue file schema:
    id            str   — uuid hex
    goal          str   — raw operator goal text
    task_type     str   — bug / feature / refactor / docs / lint / test / chore / investigation
    repo_name     str   — repo directory name under ~/Documents/GitHub/
    repo_path     str   — absolute path to repo root (may be null)
    priority      str   — normal / high / low
    source        str   — always "operator"
    submitted_at  str   — ISO-8601 UTC

Usage (foreground / debug):
    python3 -m operations_center.entrypoints.intake.main --config config.yaml

The watch loop uses inotifywait when available (near-instant pickup) and
falls back to polling every 10 seconds.
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

QUEUE_DIR = Path.home() / ".console" / "queue"
POLL_INTERVAL = 10  # seconds — used both as inotifywait timeout and fallback interval


# ── Context helpers ───────────────────────────────────────────────────────────

def _read_console_file(repo_path: Path, name: str, max_lines: int = 40) -> str:
    """Read a .console/ context file, truncated to max_lines."""
    p = repo_path / ".console" / name
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"… ({len(lines) - max_lines} lines omitted)"]
    return "\n".join(lines)


def _recent_commits(repo_path: Path, n: int = 10) -> str:
    """Return the last n commit subject lines from the repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", f"-{n}", "--oneline", "--no-decorate"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _build_elaborated_goal(item: dict) -> str:
    """
    Combine raw operator goal with repo context to produce an elaborated goal
    string suitable for PlanningContext.goal_text.

    The elaboration is lightweight — it prepends structured context so the
    downstream planning/adapter step has enough signal to scope the work
    without requiring an extra LLM call here.
    """
    repo_path = Path(item["repo_path"]) if item.get("repo_path") else None

    sections: list[str] = [f"Goal: {item['goal']}"]

    if repo_path and repo_path.exists():
        task_md = _read_console_file(repo_path, "task.md")
        if task_md:
            sections.append(f"Current task context (.console/task.md):\n{task_md}")

        backlog_md = _read_console_file(repo_path, "backlog.md", max_lines=20)
        if backlog_md:
            sections.append(f"Backlog (.console/backlog.md):\n{backlog_md}")

        commits = _recent_commits(repo_path)
        if commits:
            sections.append(f"Recent commits:\n{commits}")

    return "\n\n".join(sections)


# ── Queue processing ──────────────────────────────────────────────────────────

def _process_item(item: dict, config_path: Path, venv_python: str) -> bool:
    """
    Run one queued task through the planning → execution pipeline.
    Returns True on success, False on failure.
    """
    task_id = item.get("id", uuid.uuid4().hex[:8])
    goal = _build_elaborated_goal(item)
    task_type = item.get("task_type", "chore")
    repo_name = item.get("repo_name", "unknown")
    repo_path = item.get("repo_path") or ""
    priority = item.get("priority", "normal")

    logger.info("intake: processing task_id=%s repo=%s type=%s", task_id, repo_name, task_type)

    oc_root = Path(__file__).resolve().parents[4]  # src/operations_center/entrypoints/intake → repo root

    with tempfile.TemporaryDirectory(prefix="oc-intake-") as tmpdir:
        tmp = Path(tmpdir)

        # ── Step 1: Planning ──────────────────────────────────────────────────
        plan_cmd = [
            venv_python, "-m", "operations_center.entrypoints.worker.main",
            "--goal", goal,
            "--task-type", task_type,
            "--repo-key", repo_name,
            "--clone-url", f"file://{repo_path}" if repo_path else "https://example.invalid/placeholder.git",
            "--project-id", "intake",
            "--task-id", f"intake-{task_id}",
            "--priority", priority,
        ]

        env = _build_env(oc_root)
        plan_proc = subprocess.run(plan_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        try:
            bundle = json.loads(plan_proc.stdout)
        except Exception:
            logger.error("intake: planning produced no JSON for task_id=%s\n%s",
                         task_id, plan_proc.stderr.strip() or plan_proc.stdout.strip())
            return False

        if plan_proc.returncode != 0:
            logger.error("intake: planning failed for task_id=%s — %s",
                         task_id, bundle.get("message", "unknown error"))
            return False

        # ── Step 2: Execution ─────────────────────────────────────────────────
        bundle_file = tmp / "bundle.json"
        bundle_file.write_text(json.dumps(bundle), encoding="utf-8")

        config_file = tmp / "ops.yaml"
        shutil.copy(config_path, config_file)

        workspace = tmp / "workspace"
        workspace.mkdir()
        result_file = tmp / "result.json"

        exec_cmd = [
            venv_python, "-m", "operations_center.entrypoints.execute.main",
            "--config", str(config_file),
            "--bundle", str(bundle_file),
            "--workspace-path", str(workspace),
            "--task-branch", f"intake/{task_id}",
            "--output", str(result_file),
            "--source", "intake",
        ]

        exec_proc = subprocess.run(exec_cmd, cwd=oc_root, env=env, capture_output=True, text=True)

        if not result_file.exists():
            logger.error("intake: execute produced no result file for task_id=%s\n%s",
                         task_id, exec_proc.stderr.strip())
            return False

        outcome = json.loads(result_file.read_text(encoding="utf-8"))
        result = outcome.get("result", {})
        success = result.get("success", False)
        status = result.get("status", "unknown")

        if success:
            logger.info("intake: task_id=%s completed — status=%s", task_id, status)
        else:
            logger.warning("intake: task_id=%s failed — status=%s category=%s",
                           task_id, status, result.get("failure_category"))

        return success


def _build_env(oc_root: Path) -> dict:
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(oc_root / "src")
    return env


def _venv_python(oc_root: Path) -> str:
    p = oc_root / ".venv" / "bin" / "python"
    return str(p) if p.exists() else "python3"


# ── Watch loop ────────────────────────────────────────────────────────────────

def _has_inotifywait() -> bool:
    return shutil.which("inotifywait") is not None


def _drain_queue(config_path: Path, venv_python: str) -> None:
    """Process all current queue files."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    for f in sorted(QUEUE_DIR.glob("*.json")):
        try:
            item = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("intake: skipping malformed queue file %s", f.name)
            continue

        success = _process_item(item, config_path, venv_python)
        if success:
            f.unlink(missing_ok=True)
            logger.info("intake: removed queue file %s", f.name)
        else:
            logger.warning("intake: leaving queue file %s for inspection", f.name)


def _watch_loop_inotify(config_path: Path, venv_python: str) -> None:
    """Event-driven loop using inotifywait with POLL_INTERVAL timeout as heartbeat."""
    logger.info("intake: starting inotifywait watch on %s", QUEUE_DIR)
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    while True:
        # Drain anything already there before blocking
        _drain_queue(config_path, venv_python)

        try:
            subprocess.run(
                [
                    "inotifywait",
                    "--quiet",
                    "--event", "create",
                    "--event", "moved_to",
                    "--timeout", str(POLL_INTERVAL),
                    str(QUEUE_DIR),
                ],
                timeout=POLL_INTERVAL + 5,
            )
        except subprocess.TimeoutExpired:
            pass  # heartbeat timeout — loop and drain
        except KeyboardInterrupt:
            logger.info("intake: stopped")
            break


def _watch_loop_poll(config_path: Path, venv_python: str) -> None:
    """Polling fallback when inotifywait is not available."""
    logger.info("intake: inotifywait not found — polling every %ds", POLL_INTERVAL)
    while True:
        try:
            _drain_queue(config_path, venv_python)
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            logger.info("intake: stopped")
            break


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    global QUEUE_DIR
    parser = argparse.ArgumentParser(description="OperationsCenter intake role — operator queue processor")
    parser.add_argument("--config", required=True, type=Path, help="Path to operations_center config YAML")
    parser.add_argument("--queue-dir", type=Path, default=QUEUE_DIR, help="Override queue directory")
    parser.add_argument("--once", action="store_true", help="Drain queue once and exit (no watch loop)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [intake] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    QUEUE_DIR = args.queue_dir

    oc_root = Path(__file__).resolve().parents[4]
    python = _venv_python(oc_root)

    if args.once:
        _drain_queue(args.config, python)
        return 0

    if _has_inotifywait():
        _watch_loop_inotify(args.config, python)
    else:
        _watch_loop_poll(args.config, python)

    return 0


if __name__ == "__main__":
    sys.exit(main())
