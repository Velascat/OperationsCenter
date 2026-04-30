# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""LogScanner implementation for OperationsCenter watcher logs.

Implements Custodian's LogScanner protocol over OC's ``logs/local/watch-all/*.log``
format. Two line shapes appear:

  • JSON events (one per line):
      {"event": "watcher_restart", "role": "goal", "exit_code": 2}
      {"event": "spec_no_trigger", "ready_count": 0, ...}

  • Structured human-readable events from the board_worker / pr_review_watcher:
      00:39:00 [goal] WARNING board_worker[goal]: task_id=8110b477... \\
                                blocked status=skipped category=policy_blocked

Returns a dict with ``event`` plus any structured fields we can extract,
or ``None`` when the line is plain text (e.g. heartbeat noise).
"""
from __future__ import annotations

import json
import re

# Match the timestamped board_worker line and extract its action + status.
_BOARD_WORKER_RE = re.compile(
    r"^(?P<ts>\d{2}:\d{2}:\d{2})\s+"
    r"\[(?P<role>\w+)\]\s+"
    r"(?P<level>INFO|WARNING|ERROR)\s+"
    r"board_worker\[\w+\]:\s+"
    r"(?:task_id=(?P<task_id>\S+)\s+)?"
    r"(?P<action>claimed|processing|completed|blocked|failed|skipped)"
    r"(?:\s+status=(?P<status>\S+))?"
    r"(?:\s+category=(?P<category>\S+))?"
)


class OCLogScanner:
    """Parses OC's two log line shapes into structured event dicts."""

    def parse_event(self, line: str) -> dict | None:
        line = line.strip()
        if not line:
            return None

        # Shape 1: JSON-event lines (single object per line).
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                if isinstance(payload, dict) and "event" in payload:
                    return payload
            except (json.JSONDecodeError, ValueError):
                pass

        # Shape 2: structured board_worker line.
        m = _BOARD_WORKER_RE.match(line)
        if m:
            event = {
                "event":   f"board_worker_{m.group('action')}",
                "ts":      m.group("ts"),
                "role":    m.group("role"),
                "level":   m.group("level"),
                "action":  m.group("action"),
            }
            if m.group("task_id"):
                event["task_id"] = m.group("task_id")
            if m.group("status"):
                event["status"] = m.group("status")
            if m.group("category"):
                event["category"] = m.group("category")
            return event

        # Shape 3: prefer event= key=value style if present (some watchers emit
        # the form `event=foo key=value` without JSON wrapping).
        if "event=" in line:
            event_match = re.search(r"event=(\S+)", line)
            if event_match:
                return {"event": event_match.group(1), "raw": line}

        return None
