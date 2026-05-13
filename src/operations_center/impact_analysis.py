# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Contract-change impact analysis over the EffectiveRepoGraph.

When a planned task targets a contract-owning repo (CxRP, RxP,
PlatformManifest itself, or a project's contract repo), the operator
should see the blast radius — every repo that depends on those
contracts and would need re-validation if they change.

This module is the first real consumer of the merged EffectiveRepoGraph
that ``platform_manifest`` exposes via ``load_effective_graph``. It
walks ``depends_on_contracts_from`` edges and reports affected repos
split by visibility so private/public mixing is visible at a glance.
"""
from __future__ import annotations

from dataclasses import dataclass

from platform_manifest import RepoGraph, RepoNode, Visibility


@dataclass(frozen=True)
class ContractImpactSummary:
    """Result of a contract-change impact analysis for one target repo."""

    target: RepoNode
    affected: tuple[RepoNode, ...] = ()

    @property
    def public_affected(self) -> tuple[RepoNode, ...]:
        return tuple(n for n in self.affected if n.visibility is Visibility.PUBLIC)

    @property
    def private_affected(self) -> tuple[RepoNode, ...]:
        return tuple(n for n in self.affected if n.visibility is Visibility.PRIVATE)

    def has_impact(self) -> bool:
        return len(self.affected) > 0

    def render_summary(self) -> str:
        """Compact human-readable single-line summary for logs/observability."""
        if not self.affected:
            return f"contract change in {self.target.canonical_name}: no consumers"
        names = [n.canonical_name for n in self.affected]
        return (
            f"contract change in {self.target.canonical_name} affects "
            f"{len(self.affected)} consumer(s): {', '.join(names)}"
        )


def compute_contract_impact(
    graph: RepoGraph,
    target_name: str,
) -> ContractImpactSummary | None:
    """Return the impact summary for a contract change in ``target_name``.

    ``target_name`` is matched case-insensitively against canonical and
    projection labels via the graph's resolver. Returns ``None`` if
    no node matches (caller decides whether that's an error or a
    pass-through — typically pass-through for non-contract repos).
    """
    node = graph.resolve(target_name)
    if node is None:
        return None
    affected = tuple(graph.affected_by_contract_change(node.repo_id))
    return ContractImpactSummary(target=node, affected=affected)


__all__ = [
    "ContractImpactSummary",
    "compute_contract_impact",
]
