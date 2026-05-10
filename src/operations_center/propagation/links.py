# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Parent-child link metadata embedded in every propagated Plane task.

When the propagator creates a downstream task, it appends a structured
HTML-comment block to the task body. Operators (and a future
``operations-center-propagation-links`` CLI) can grep for these markers
to reconstruct the propagation chain without a database.

Format is deliberately stable + line-oriented so a `grep -A 10` works:

    <!-- propagation:source -->
    target: CxRP
    target_repo_id: cxrp
    target_version: 7e8624c
    edge_type: depends_on_contracts_from
    triggered_at: 2026-05-08T14:23:11+00:00
    propagator_run_id: 1f9c4a2e-...
    <!-- /propagation:source -->
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from textwrap import dedent


PARENT_LINK_TEMPLATE = dedent(
    """\
    <!-- propagation:source -->
    target: {target}
    target_repo_id: {target_repo_id}
    target_version: {target_version}
    edge_type: {edge_type}
    triggered_at: {triggered_at}
    propagator_run_id: {propagator_run_id}
    <!-- /propagation:source -->
    """
).rstrip("\n")


@dataclass(frozen=True)
class ParentLink:
    """The fields a downstream task carries about its parent change."""

    target: str
    target_repo_id: str
    target_version: str
    edge_type: str
    triggered_at: datetime
    propagator_run_id: str

    def render(self) -> str:
        """Emit the canonical HTML-comment block."""
        return PARENT_LINK_TEMPLATE.format(
            target=self.target,
            target_repo_id=self.target_repo_id,
            target_version=self.target_version,
            edge_type=self.edge_type,
            triggered_at=self.triggered_at.isoformat(),
            propagator_run_id=self.propagator_run_id,
        )

    def to_dict(self) -> dict[str, str]:
        """Serializable dict — used by PropagationRecord artifacts."""
        d = asdict(self)
        d["triggered_at"] = self.triggered_at.isoformat()
        return d


def format_parent_link(link: ParentLink) -> str:
    """Convenience wrapper — same as ``link.render()``."""
    return link.render()


__all__ = ["PARENT_LINK_TEMPLATE", "ParentLink", "format_parent_link"]
