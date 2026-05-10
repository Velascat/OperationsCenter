# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PlaneClient → propagation `_TaskCreator` adapter.

Wraps the existing `operations_center.adapters.plane.PlaneClient` so
the propagator stays decoupled from Plane's API shape. The adapter
honors `promote_to_ready` by calling `transition_issue` to "Ready for
AI" after creation; otherwise the task stays in the default "Backlog"
state.
"""
from __future__ import annotations

from dataclasses import dataclass

from operations_center.adapters.plane.client import PlaneClient


@dataclass
class PlaneTaskCreator:
    """Adapter implementing the propagator's `_TaskCreator` protocol."""

    client: PlaneClient
    backlog_state: str = "Backlog"
    ready_state: str = "Ready for AI"

    def create_issue(
        self,
        *,
        title: str,
        body: str,
        labels: tuple[str, ...],
        promote_to_ready: bool,
    ) -> str:
        """Create the Plane issue. Promote to Ready if the policy says so."""
        result = self.client.create_issue(
            name=title,
            description=body,
            state=self.backlog_state,
            label_names=list(labels),
        )
        issue_id = str(result.get("id", ""))
        if promote_to_ready and issue_id:
            try:
                self.client.transition_issue(issue_id, self.ready_state)
            except Exception:  # noqa: BLE001 — Backlog placement is the safety floor
                pass
        return issue_id


__all__ = ["PlaneTaskCreator"]
