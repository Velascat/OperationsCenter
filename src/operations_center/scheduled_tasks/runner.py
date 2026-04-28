"""Periodic Plane task seeder — implementation."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operations_center.config.settings import ScheduledTask, Settings

logger = logging.getLogger(__name__)

_STATE_FILE = Path("state/scheduled_tasks_last_run.json")
_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([mhdw])\s*$", re.IGNORECASE)
_AT_RE       = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_DAY_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


# ── parsing helpers ──────────────────────────────────────────────────────────

def _parse_every(s: str) -> int:
    """Parse '1w' / '6h' / '30m' → seconds. Raises on malformed input."""
    m = _INTERVAL_RE.match(s)
    if not m:
        raise ValueError(f"every must be <num><unit> with unit in m/h/d/w; got {s!r}")
    n = int(m.group(1))
    unit = m.group(2).lower()
    multiplier = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return n * multiplier


def _parse_at(s: str) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute) in UTC. Raises on malformed input."""
    m = _AT_RE.match(s)
    if not m:
        raise ValueError(f"at must be HH:MM; got {s!r}")
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        raise ValueError(f"at hour/minute out of range: {s!r}")
    return h, mm


def _task_key(task: "ScheduledTask") -> str:
    """Stable identifier for a scheduled task — title + repo_key hash.

    Renaming a task therefore restarts its schedule. That's intentional:
    the operator is signalling "this is a different task" by changing the
    title.
    """
    raw = f"{task.title}|{task.repo_key}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ── due-check ────────────────────────────────────────────────────────────────

@dataclass
class _DueTask:
    task: "ScheduledTask"
    key: str
    last_run: datetime | None


def _is_due(task: "ScheduledTask", last_run: datetime | None, now: datetime,
            *, slack_seconds: int = 300) -> bool:
    """Return True when the task should fire on this cycle.

    `slack_seconds` allows the `at` anchor to match within a window — a
    propose cycle running every ~120s can still hit a 09:00 anchor even if
    it polls at 09:01.
    """
    try:
        interval_s = _parse_every(task.every)
    except ValueError as exc:
        logger.warning("scheduled_tasks: skipping malformed task %r — %s", task.title, exc)
        return False

    # Interval gate (always applies)
    if last_run is not None:
        elapsed = (now - last_run).total_seconds()
        if elapsed < interval_s:
            return False

    # Weekday gate
    if task.on_days:
        normalized = {d.strip().lower()[:3] for d in task.on_days if d}
        unknown = normalized - _DAY_NAMES.keys()
        if unknown:
            logger.warning("scheduled_tasks: unknown day name(s) %s in task %r", unknown, task.title)
        weekday = now.weekday()
        if weekday not in {_DAY_NAMES[d] for d in normalized & _DAY_NAMES.keys()}:
            return False

    # Time-of-day anchor
    if task.at:
        try:
            anchor_h, anchor_m = _parse_at(task.at)
        except ValueError as exc:
            logger.warning("scheduled_tasks: bad `at` for %r — %s", task.title, exc)
            return False
        # Compute today's anchor moment in UTC
        anchor_today = now.replace(hour=anchor_h, minute=anchor_m, second=0, microsecond=0)
        delta = abs((now - anchor_today).total_seconds())
        if delta > slack_seconds:
            return False

    return True


def due_tasks(
    settings: "Settings",
    *,
    state_file: Path | None = None,
    now: datetime | None = None,
) -> list["ScheduledTask"]:
    """Return scheduled tasks that should fire on this cycle."""
    state_path = state_file or _STATE_FILE
    when = now or datetime.now(UTC)
    raw = (settings.scheduled_tasks or [])
    if not raw:
        return []

    last_run_map: dict[str, str] = {}
    if state_path.exists():
        try:
            last_run_map = json.loads(state_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.warning("scheduled_tasks: state file unreadable, treating as empty — %s", exc)

    out: list[ScheduledTask] = []
    for task in raw:
        key = _task_key(task)
        last_run_iso = last_run_map.get(key)
        last_run = None
        if last_run_iso:
            try:
                last_run = datetime.fromisoformat(last_run_iso.replace("Z", "+00:00"))
            except Exception:
                pass
        if _is_due(task, last_run, when):
            out.append(task)
    return out


# ── runner ───────────────────────────────────────────────────────────────────

class ScheduledTaskRunner:
    """Drive scheduled-task evaluation against a Plane client.

    Usage from autonomy_cycle / propose path:
        runner = ScheduledTaskRunner(plane_client, settings)
        created_ids = runner.tick()
    """

    def __init__(self, plane_client, settings: "Settings", *, state_file: Path | None = None) -> None:
        self._client = plane_client
        self._settings = settings
        self._state_file = state_file or _STATE_FILE

    def tick(self, *, now: datetime | None = None) -> list[str]:
        """Evaluate schedules and create Plane tasks for any that fire.

        Returns list of created Plane task ids. State file is updated only
        for tasks whose creation succeeded — failures are logged and the
        next cycle will retry.
        """
        when = now or datetime.now(UTC)
        due = due_tasks(self._settings, state_file=self._state_file, now=when)
        if not due:
            return []

        created_ids: list[str] = []
        # Load → mutate → save state once at the end, so partial failures
        # don't lose progress.
        last_run_map: dict[str, str] = {}
        if self._state_file.exists():
            try:
                last_run_map = json.loads(self._state_file.read_text(encoding="utf-8")) or {}
            except Exception:
                last_run_map = {}

        for task in due:
            try:
                description = (
                    f"## Goal\n{task.goal}\n\n"
                    f"## Execution\n"
                    f"repo: {task.repo_key}\n"
                    f"mode: {task.kind}\n"
                    f"\n## Provenance\n"
                    f"source: scheduled_tasks\n"
                    f"every: {task.every}\n"
                )
                labels = [
                    f"task-kind: {task.kind}",
                    f"repo: {task.repo_key}",
                    "source: scheduled-task",
                    "source: autonomy",  # trusted-source bypass
                ]
                issue = self._client.create_issue(
                    name=task.title,
                    description=description,
                    state="Ready for AI",
                    label_names=labels,
                )
                new_id = str(issue.get("id", ""))
                if new_id:
                    created_ids.append(new_id)
                    last_run_map[_task_key(task)] = when.isoformat()
                    logger.info(
                        '{"event": "scheduled_task_created", "task_id": "%s", "title": "%s", '
                        '"every": "%s", "key": "%s"}',
                        new_id, task.title, task.every, _task_key(task),
                    )
            except Exception as exc:
                logger.warning(
                    "scheduled_tasks: failed to create %r — %s (will retry next cycle)",
                    task.title, exc,
                )

        if created_ids:
            try:
                self._state_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._state_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(last_run_map, indent=2), encoding="utf-8")
                tmp.rename(self._state_file)
            except OSError as exc:
                logger.warning("scheduled_tasks: failed to persist last_run state — %s", exc)

        return created_ids
