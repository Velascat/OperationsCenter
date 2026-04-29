# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from datetime import datetime

import httpx


def post_escalation(
    webhook_url: str,
    *,
    classification: str,
    count: int,
    task_ids: list[str],
    now: datetime,
) -> None:
    """POST an escalation payload to *webhook_url*.

    Caller is responsible for spam-guard (checking cooldown before calling).
    Swallows all exceptions — notification is best-effort.
    """
    if not webhook_url:
        return
    payload = {
        "event": "escalation_threshold_reached",
        "classification": classification,
        "count": count,
        "task_ids": task_ids,
        "timestamp": now.isoformat(),
    }
    try:
        with httpx.Client(timeout=10) as http:
            http.post(webhook_url, json=payload)
    except Exception:
        pass
