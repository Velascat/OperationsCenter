# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ER-001 — Repo Graph primitive tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from operations_center.repo_graph import (
    RepoEdge,
    RepoEdgeType,
    RepoGraph,
    RepoGraphConfigError,
    RepoNode,
    load_repo_graph,
)
from operations_center.repo_graph.cli import _default_config_path, app

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LIVE_CONFIG = _REPO_ROOT / "config" / "repo_graph.yaml"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModelBuild:
    def _node(self, repo_id: str, canonical: str, legacy: tuple[str, ...] = ()) -> RepoNode:
        return RepoNode(repo_id=repo_id, canonical_name=canonical, legacy_names=legacy)

    def test_build_indexes_canonical_and_legacy(self) -> None:
        g = RepoGraph.build(
            nodes=[self._node("oc", "OperationsCenter", ("ControlPlane",))],
            edges=[],
        )
        assert g.resolve("OperationsCenter").repo_id == "oc"
        assert g.resolve("ControlPlane").repo_id == "oc"
        assert g.resolve("controlplane").repo_id == "oc"  # case-insensitive
        assert g.resolve("nope") is None

    def test_duplicate_repo_id_rejected(self) -> None:
        with pytest.raises(RepoGraphConfigError, match="duplicate repo_id"):
            RepoGraph.build(
                nodes=[self._node("a", "A"), self._node("a", "B")],
                edges=[],
            )

    def test_alias_collision_rejected(self) -> None:
        with pytest.raises(RepoGraphConfigError, match="maps to both"):
            RepoGraph.build(
                nodes=[
                    self._node("a", "A", legacy=("Common",)),
                    self._node("b", "B", legacy=("Common",)),
                ],
                edges=[],
            )

    def test_edge_to_unknown_node_rejected(self) -> None:
        with pytest.raises(RepoGraphConfigError, match="unknown dst"):
            RepoGraph.build(
                nodes=[self._node("a", "A")],
                edges=[RepoEdge(src="a", dst="ghost", type=RepoEdgeType.DISPATCHES_TO)],
            )


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@pytest.fixture
def small_graph() -> RepoGraph:
    return RepoGraph.build(
        nodes=[
            RepoNode(repo_id="oc", canonical_name="OperationsCenter", legacy_names=("ControlPlane",)),
            RepoNode(repo_id="sb", canonical_name="SwitchBoard"),
            RepoNode(repo_id="op", canonical_name="OperatorConsole", legacy_names=("FOB",)),
            RepoNode(repo_id="cx", canonical_name="CxRP"),
        ],
        edges=[
            RepoEdge(src="op", dst="oc", type=RepoEdgeType.DISPATCHES_TO),
            RepoEdge(src="oc", dst="sb", type=RepoEdgeType.ROUTES_THROUGH),
            RepoEdge(src="oc", dst="cx", type=RepoEdgeType.DEPENDS_ON_CONTRACTS_FROM),
            RepoEdge(src="sb", dst="cx", type=RepoEdgeType.DEPENDS_ON_CONTRACTS_FROM),
        ],
    )


class TestQueries:
    def test_upstream_returns_direct_targets(self, small_graph: RepoGraph) -> None:
        names = [n.canonical_name for n in small_graph.upstream("oc")]
        assert names == ["CxRP", "SwitchBoard"]

    def test_downstream_returns_direct_sources(self, small_graph: RepoGraph) -> None:
        names = [n.canonical_name for n in small_graph.downstream("oc")]
        assert names == ["OperatorConsole"]

    def test_upstream_unknown_repo_raises(self, small_graph: RepoGraph) -> None:
        with pytest.raises(KeyError):
            small_graph.upstream("ghost")

    def test_affected_by_contract_change(self, small_graph: RepoGraph) -> None:
        consumers = [n.canonical_name for n in small_graph.affected_by_contract_change("cx")]
        assert consumers == ["OperationsCenter", "SwitchBoard"]

    def test_affected_excludes_non_contract_edges(self, small_graph: RepoGraph) -> None:
        # OC dispatches_to is not a contract dependency, so OC should NOT
        # appear as affected by an OperatorConsole change.
        affected = small_graph.affected_by_contract_change("op")
        assert affected == []


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_load_minimal(self, tmp_path: Path) -> None:
        cfg = tmp_path / "g.yaml"
        cfg.write_text(
            "repos:\n"
            "  oc: {canonical_name: OperationsCenter, legacy_names: [ControlPlane]}\n"
            "  cx: {canonical_name: CxRP}\n"
            "edges:\n"
            "  - {from: OperationsCenter, to: CxRP, type: depends_on_contracts_from}\n",
            encoding="utf-8",
        )
        g = load_repo_graph(cfg)
        assert {n.repo_id for n in g.list_nodes()} == {"oc", "cx"}
        assert g.affected_by_contract_change("cx")[0].canonical_name == "OperationsCenter"

    def test_load_unknown_edge_type_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "g.yaml"
        cfg.write_text(
            "repos:\n"
            "  a: {canonical_name: A}\n"
            "  b: {canonical_name: B}\n"
            "edges:\n"
            "  - {from: A, to: B, type: bogus_edge}\n",
            encoding="utf-8",
        )
        with pytest.raises(RepoGraphConfigError, match="unknown type"):
            load_repo_graph(cfg)

    def test_load_missing_canonical_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "g.yaml"
        cfg.write_text("repos:\n  bad: {legacy_names: [X]}\n", encoding="utf-8")
        with pytest.raises(RepoGraphConfigError, match="canonical_name"):
            load_repo_graph(cfg)

    def test_load_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(RepoGraphConfigError, match="not found"):
            load_repo_graph(tmp_path / "nope.yaml")


# ---------------------------------------------------------------------------
# Live config
# ---------------------------------------------------------------------------


class TestLiveConfig:
    """The shipped config/repo_graph.yaml must load cleanly and resolve the
    canonical legacy aliases the rest of the platform relies on."""

    def test_live_loads(self) -> None:
        graph = load_repo_graph(_LIVE_CONFIG)
        assert graph.resolve("OperationsCenter") is not None
        assert graph.resolve("SwitchBoard") is not None

    def test_live_legacy_aliases_resolve(self) -> None:
        graph = load_repo_graph(_LIVE_CONFIG)
        assert graph.resolve("ControlPlane").canonical_name == "OperationsCenter"
        assert graph.resolve("FOB").canonical_name == "OperatorConsole"
        assert graph.resolve("ExecutionContractProtocol").canonical_name == "CxRP"

    def test_live_contract_change_in_cxrp_lists_consumers(self) -> None:
        graph = load_repo_graph(_LIVE_CONFIG)
        consumers = {n.canonical_name for n in graph.affected_by_contract_change("cxrp")}
        assert {"OperationsCenter", "SwitchBoard", "OperatorConsole"}.issubset(consumers)

    def test_default_config_path_resolves(self) -> None:
        assert _default_config_path() == _LIVE_CONFIG


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_list(self) -> None:
        result = self.runner.invoke(app, ["list"])
        assert result.exit_code == 0, result.output
        assert "OperationsCenter" in result.output

    def test_resolve_legacy(self) -> None:
        result = self.runner.invoke(app, ["resolve", "ControlPlane"])
        assert result.exit_code == 0, result.output
        assert "OperationsCenter" in result.output

    def test_resolve_unknown_exits_nonzero(self) -> None:
        result = self.runner.invoke(app, ["resolve", "Nope"])
        assert result.exit_code != 0

    def test_impact_lists_consumers(self) -> None:
        result = self.runner.invoke(app, ["impact", "cxrp"])
        assert result.exit_code == 0, result.output
        assert "OperationsCenter" in result.output
        assert "SwitchBoard" in result.output
