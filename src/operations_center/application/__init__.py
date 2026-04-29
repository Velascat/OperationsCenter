# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from typing import Any

__all__ = ["ChangedFilePolicyChecker", "TaskParser"]


def __getattr__(name: str) -> Any:
    if name == "ChangedFilePolicyChecker":
        from operations_center.application.scope_policy import ChangedFilePolicyChecker

        return ChangedFilePolicyChecker
    if name == "TaskParser":
        from operations_center.application.task_parser import TaskParser

        return TaskParser
    raise AttributeError(name)
