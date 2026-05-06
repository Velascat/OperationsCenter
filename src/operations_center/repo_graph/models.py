# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Repo Graph models — RepoNode, RepoEdge, RepoGraph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RepoEdgeType(str, Enum):
    """v1 edge vocabulary. Add new values only when a real query needs them."""

    DEPENDS_ON_CONTRACTS_FROM = "depends_on_contracts_from"
    DISPATCHES_TO = "dispatches_to"
    ROUTES_THROUGH = "routes_through"


class RepoGraphConfigError(ValueError):
    """Raised when the repo graph YAML is malformed or internally inconsistent."""


@dataclass(frozen=True)
class RepoNode:
    repo_id: str
    canonical_name: str
    legacy_names: tuple[str, ...] = ()
    local_path: str | None = None
    github_url: str | None = None
    runtime_role: str | None = None


@dataclass(frozen=True)
class RepoEdge:
    src: str  # repo_id
    dst: str  # repo_id
    type: RepoEdgeType


@dataclass
class RepoGraph:
    nodes: dict[str, RepoNode] = field(default_factory=dict)  # keyed by repo_id
    edges: tuple[RepoEdge, ...] = ()
    # name index built at construction time: lowercased canonical & legacy → repo_id
    _name_index: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def build(
        cls,
        nodes: list[RepoNode],
        edges: list[RepoEdge],
    ) -> "RepoGraph":
        node_map: dict[str, RepoNode] = {}
        name_index: dict[str, str] = {}
        for node in nodes:
            if node.repo_id in node_map:
                raise RepoGraphConfigError(f"duplicate repo_id: {node.repo_id}")
            node_map[node.repo_id] = node
            for alias in (node.canonical_name, *node.legacy_names):
                key = alias.lower()
                if key in name_index and name_index[key] != node.repo_id:
                    raise RepoGraphConfigError(
                        f"name '{alias}' maps to both "
                        f"'{name_index[key]}' and '{node.repo_id}'"
                    )
                name_index[key] = node.repo_id
        for edge in edges:
            if edge.src not in node_map:
                raise RepoGraphConfigError(
                    f"edge {edge.type.value} references unknown src '{edge.src}'"
                )
            if edge.dst not in node_map:
                raise RepoGraphConfigError(
                    f"edge {edge.type.value} references unknown dst '{edge.dst}'"
                )
        return cls(nodes=node_map, edges=tuple(edges), _name_index=name_index)

    # -- queries ---------------------------------------------------------

    def list_nodes(self) -> list[RepoNode]:
        """All known repos in stable canonical-name order."""
        return sorted(self.nodes.values(), key=lambda n: n.canonical_name)

    def resolve(self, name: str) -> RepoNode | None:
        """Resolve a canonical or legacy name to its node. Case-insensitive."""
        repo_id = self._name_index.get(name.lower())
        if repo_id is None:
            return None
        return self.nodes[repo_id]

    def upstream(self, repo_id: str) -> list[RepoNode]:
        """Direct upstream nodes — i.e., nodes this repo points to via its outgoing edges."""
        if repo_id not in self.nodes:
            raise KeyError(repo_id)
        targets = {e.dst for e in self.edges if e.src == repo_id}
        return [self.nodes[t] for t in sorted(targets)]

    def downstream(self, repo_id: str) -> list[RepoNode]:
        """Direct downstream nodes — i.e., nodes that point to this repo."""
        if repo_id not in self.nodes:
            raise KeyError(repo_id)
        sources = {e.src for e in self.edges if e.dst == repo_id}
        return [self.nodes[s] for s in sorted(sources)]

    def affected_by_contract_change(self, repo_id: str) -> list[RepoNode]:
        """Repos that depend on `repo_id` via DEPENDS_ON_CONTRACTS_FROM."""
        if repo_id not in self.nodes:
            raise KeyError(repo_id)
        consumers = {
            e.src
            for e in self.edges
            if e.dst == repo_id and e.type == RepoEdgeType.DEPENDS_ON_CONTRACTS_FROM
        }
        return [self.nodes[c] for c in sorted(consumers)]
