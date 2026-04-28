"""Scheduled task seeder — periodically injects Plane work items.

What this is: a generator. Each propose cycle, we check every entry in
``settings.scheduled_tasks`` and create a Plane Ready-for-AI task for any
that is "due". State is persisted in
``state/scheduled_tasks_last_run.json`` keyed by a hash of the task title
+ repo_key so renames create a new schedule.

What this is NOT: a cron daemon. There's no separate scheduler process.
The "schedule" runs at whatever cadence the propose cycle runs at; if the
propose cycle is paused the scheduled tasks pause too. This is by design
— scheduled tasks should follow the same maintenance windows / health
gates as everything else.

Schema (config YAML):
    scheduled_tasks:
      - every:    "1w"                # required: <num><unit>, unit in m/h/d/w
        at:       "09:00"             # optional UTC time-of-day anchor
        on_days:  [mon]               # optional weekday gate
        title:    "Weekly dependency audit"
        goal:     "Check for outdated dependencies."
        repo_key: "OperationsCenter"
        kind:     "goal"
"""
from __future__ import annotations

from .runner import ScheduledTaskRunner, due_tasks

__all__ = ["ScheduledTaskRunner", "due_tasks"]
