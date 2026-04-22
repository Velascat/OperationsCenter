from __future__ import annotations

from typing import Any

__all__ = ["ChangedFilePolicyChecker", "TaskParser"]


def __getattr__(name: str) -> Any:
    if name == "ChangedFilePolicyChecker":
        from control_plane.application.scope_policy import ChangedFilePolicyChecker

        return ChangedFilePolicyChecker
    if name == "TaskParser":
        from control_plane.application.task_parser import TaskParser

        return TaskParser
    raise AttributeError(name)
