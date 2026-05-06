# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ER-001 — Repo Graph primitive.

Treat repos as graph nodes with canonical identity + legacy-name resolution +
direct upstream/downstream queries. The graph is read-only context for
OperationsCenter planning and for SwitchBoard lane-decision input.

Public API:
  load_repo_graph(path: Path) -> RepoGraph
  RepoGraph.resolve(name) -> RepoNode | None
  RepoGraph.upstream(repo_id) -> list[RepoNode]
  RepoGraph.downstream(repo_id) -> list[RepoNode]
  RepoGraph.affected_by_contract_change(repo_id) -> list[RepoNode]
"""

from .loader import load_repo_graph
from .models import (
    RepoEdge,
    RepoEdgeType,
    RepoGraph,
    RepoGraphConfigError,
    RepoNode,
)

__all__ = [
    "RepoEdge",
    "RepoEdgeType",
    "RepoGraph",
    "RepoGraphConfigError",
    "RepoNode",
    "load_repo_graph",
]
