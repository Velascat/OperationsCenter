# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Bounded synchronous sleep helper.

Used by ``ExecutionCoordinator`` to enforce ``RecoveryOutcome.delay_seconds``
between retry attempts. Backoff is in-loop (synchronous) by design — the
recovery loop is not a scheduler and must not enqueue or spawn anything.
"""

from __future__ import annotations

import time


def bounded_sleep(delay_seconds: float, max_delay_seconds: float) -> float:
    """Sleep for ``delay_seconds`` clamped to ``[0, max_delay_seconds]``.

    Returns the actual slept duration (the clamped value). Callers should
    record this on the corresponding ``RecoveryAction.delay_seconds``.
    """
    delay = max(0.0, min(float(delay_seconds), float(max_delay_seconds)))
    time.sleep(delay)
    return delay


__all__ = ["bounded_sleep"]
