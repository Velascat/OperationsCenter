# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""YAML loader for the Repo Graph config.

Format:

    repos:
      operations_center:
        canonical_name: OperationsCenter
        legacy_names: [ControlPlane]
        local_path: ~/Documents/GitHub/OperationsCenter
        github_url: https://github.com/Velascat/OperationsCenter
        runtime_role: orchestration

    edges:
      - {from: OperatorConsole, to: OperationsCenter, type: dispatches_to}
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import (
    RepoEdge,
    RepoEdgeType,
    RepoGraph,
    RepoGraphConfigError,
    RepoNode,
)


def default_config_path() -> Path:
    """Path to the shipped ``config/repo_graph.yaml``.

    Located via this file's location so callers don't need to know the
    repo layout. Returns the path even if the file does not exist.
    """
    return Path(__file__).resolve().parents[3] / "config" / "repo_graph.yaml"


_cached_default: RepoGraph | None = None


def load_default_repo_graph() -> RepoGraph:
    """Load + cache the shipped repo graph. Safe to call from coordinator
    construction sites; subsequent calls reuse the parsed graph."""
    global _cached_default
    if _cached_default is None:
        _cached_default = load_repo_graph(default_config_path())
    return _cached_default


def load_repo_graph(path: Path) -> RepoGraph:
    if not path.exists():
        raise RepoGraphConfigError(f"repo graph config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RepoGraphConfigError(f"repo graph config root must be a mapping: {path}")

    repos_raw = raw.get("repos") or {}
    edges_raw = raw.get("edges") or []
    if not isinstance(repos_raw, dict):
        raise RepoGraphConfigError("'repos' must be a mapping of repo_id → fields")
    if not isinstance(edges_raw, list):
        raise RepoGraphConfigError("'edges' must be a list of {from,to,type} mappings")

    nodes: list[RepoNode] = []
    for repo_id, fields in repos_raw.items():
        if not isinstance(fields, dict):
            raise RepoGraphConfigError(f"repo '{repo_id}' fields must be a mapping")
        canonical = fields.get("canonical_name")
        if not canonical or not isinstance(canonical, str):
            raise RepoGraphConfigError(f"repo '{repo_id}' missing canonical_name")
        legacy = fields.get("legacy_names") or []
        if not isinstance(legacy, list) or not all(isinstance(s, str) for s in legacy):
            raise RepoGraphConfigError(
                f"repo '{repo_id}' legacy_names must be a list of strings"
            )
        nodes.append(
            RepoNode(
                repo_id=str(repo_id),
                canonical_name=canonical,
                legacy_names=tuple(legacy),
                local_path=_opt_str(fields, "local_path"),
                github_url=_opt_str(fields, "github_url"),
                runtime_role=_opt_str(fields, "runtime_role"),
            )
        )

    # Build a quick canonical→repo_id map so edges can name canonical names.
    name_to_id: dict[str, str] = {}
    for node in nodes:
        name_to_id[node.canonical_name.lower()] = node.repo_id
        name_to_id[node.repo_id.lower()] = node.repo_id

    edges: list[RepoEdge] = []
    for idx, item in enumerate(edges_raw):
        if not isinstance(item, dict):
            raise RepoGraphConfigError(f"edge #{idx} must be a mapping")
        src_name = item.get("from")
        dst_name = item.get("to")
        edge_type_raw = item.get("type")
        if not (src_name and dst_name and edge_type_raw):
            raise RepoGraphConfigError(
                f"edge #{idx} requires 'from', 'to', and 'type'"
            )
        try:
            edge_type = RepoEdgeType(edge_type_raw)
        except ValueError as exc:
            raise RepoGraphConfigError(
                f"edge #{idx} has unknown type '{edge_type_raw}'; "
                f"allowed: {[t.value for t in RepoEdgeType]}"
            ) from exc
        src_id = name_to_id.get(str(src_name).lower())
        dst_id = name_to_id.get(str(dst_name).lower())
        if src_id is None:
            raise RepoGraphConfigError(f"edge #{idx} 'from' unknown: {src_name}")
        if dst_id is None:
            raise RepoGraphConfigError(f"edge #{idx} 'to' unknown: {dst_name}")
        edges.append(RepoEdge(src=src_id, dst=dst_id, type=edge_type))

    return RepoGraph.build(nodes=nodes, edges=edges)


def _opt_str(fields: dict[str, Any], key: str) -> str | None:
    val = fields.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        raise RepoGraphConfigError(f"field '{key}' must be a string if present")
    return val
