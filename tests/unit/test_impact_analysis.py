# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for operations_center.impact_analysis."""
from __future__ import annotations

from pathlib import Path

from platform_manifest import Visibility

from operations_center.impact_analysis import (
    compute_contract_impact,
)
from operations_center.repo_graph_factory import build_effective_repo_graph


# ---------------------------------------------------------------------------
# Default platform manifest
# ---------------------------------------------------------------------------


class TestPlatformContractImpact:
    """Sanity-check impact on the bundled platform manifest."""

    def test_cxrp_contract_change_affects_three_consumers(self) -> None:
        g = build_effective_repo_graph()
        summary = compute_contract_impact(g, "CxRP")
        assert summary is not None
        assert summary.has_impact()
        names = {n.canonical_name for n in summary.affected}
        assert {"OperationsCenter", "SwitchBoard", "OperatorConsole"}.issubset(names)
        # All affected are public in the platform-only base
        assert all(n.visibility is Visibility.PUBLIC for n in summary.affected)
        assert len(summary.private_affected) == 0

    def test_unknown_repo_returns_none(self) -> None:
        g = build_effective_repo_graph()
        assert compute_contract_impact(g, "ghost-repo") is None

    def test_legacy_alias_resolves(self) -> None:
        g = build_effective_repo_graph()
        # ExecutionContractProtocol is the legacy alias for CxRP.
        summary = compute_contract_impact(g, "ExecutionContractProtocol")
        assert summary is not None
        assert summary.target.canonical_name == "CxRP"
        assert summary.has_impact()

    def test_non_contract_repo_has_no_consumers(self) -> None:
        g = build_effective_repo_graph()
        # OperatorConsole is at the edge — nothing depends on its
        # contracts (it's the dispatcher, not a contract owner).
        summary = compute_contract_impact(g, "OperatorConsole")
        assert summary is not None
        assert not summary.has_impact()
        assert summary.affected == ()


# ---------------------------------------------------------------------------
# Effective graph with project layer — private + public mix
# ---------------------------------------------------------------------------


class TestEffectiveGraphWithProject:
    def _project_yaml_with_private_contract(self) -> str:
        return (
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  vfa_contracts:\n'
            '    canonical_name: VFAContracts\n'
            '    visibility: private\n'
            '    runtime_role: contracts\n'
            '  vfa_api:\n'
            '    canonical_name: VFAApi\n'
            '    visibility: private\n'
            '  vfa_worker:\n'
            '    canonical_name: VFAWorker\n'
            '    visibility: private\n'
            'edges:\n'
            '  - {from: VFAApi, to: VFAContracts, type: depends_on_contracts_from}\n'
            '  - {from: VFAWorker, to: VFAContracts, type: depends_on_contracts_from}\n'
        )

    def test_private_contract_change_affects_private_consumers(
        self, tmp_path: Path
    ) -> None:
        proj = tmp_path / "project.yaml"
        proj.write_text(self._project_yaml_with_private_contract(), encoding="utf-8")
        g = build_effective_repo_graph(project_manifest_path=proj)
        summary = compute_contract_impact(g, "VFAContracts")
        assert summary is not None
        assert summary.has_impact()
        names = {n.canonical_name for n in summary.affected}
        assert names == {"VFAApi", "VFAWorker"}
        # All affected are private (project layer)
        assert all(n.visibility is Visibility.PRIVATE for n in summary.affected)
        assert len(summary.private_affected) == 2
        assert len(summary.public_affected) == 0

    def test_platform_contract_change_picks_up_private_consumers_when_present(
        self, tmp_path: Path
    ) -> None:
        # A project repo depends on CxRP — impact on CxRP should now
        # surface that private consumer alongside the public ones.
        proj = tmp_path / "project.yaml"
        proj.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  vfa_api:\n'
            '    canonical_name: VFAApi\n'
            '    visibility: private\n'
            'edges:\n'
            '  - {from: VFAApi, to: CxRP, type: depends_on_contracts_from}\n',
            encoding="utf-8",
        )
        g = build_effective_repo_graph(project_manifest_path=proj)
        summary = compute_contract_impact(g, "CxRP")
        assert summary is not None
        names = {n.canonical_name for n in summary.affected}
        assert {"OperationsCenter", "SwitchBoard", "OperatorConsole", "VFAApi"} == names
        assert len(summary.public_affected) == 3
        assert len(summary.private_affected) == 1


# ---------------------------------------------------------------------------
# Render summary
# ---------------------------------------------------------------------------


class TestRender:
    def test_no_impact_renders_compactly(self) -> None:
        g = build_effective_repo_graph()
        summary = compute_contract_impact(g, "OperatorConsole")
        assert summary is not None
        rendered = summary.render_summary()
        assert "no consumers" in rendered
        assert "OperatorConsole" in rendered

    def test_with_impact_renders_count_and_names(self) -> None:
        g = build_effective_repo_graph()
        summary = compute_contract_impact(g, "CxRP")
        assert summary is not None
        rendered = summary.render_summary()
        assert "CxRP" in rendered
        assert "OperationsCenter" in rendered
        assert "SwitchBoard" in rendered
        assert "OperatorConsole" in rendered
        assert "consumer" in rendered


# ---------------------------------------------------------------------------
# ContractImpactSummary structural smoke
# ---------------------------------------------------------------------------


class TestSummaryStructure:
    def test_visibility_partition_disjoint(self) -> None:
        g = build_effective_repo_graph()
        summary = compute_contract_impact(g, "CxRP")
        assert summary is not None
        public_ids = {n.repo_id for n in summary.public_affected}
        private_ids = {n.repo_id for n in summary.private_affected}
        assert public_ids.isdisjoint(private_ids)
        assert public_ids | private_ids == {n.repo_id for n in summary.affected}


# ---------------------------------------------------------------------------
# Effective graph composed via WorkScopeManifest — impact spans includes
# ---------------------------------------------------------------------------


class TestEffectiveGraphWithWorkScope:
    """v0.9.0+ — impact analysis must work on graphs built from a WorkScopeManifest."""

    def test_impact_spans_two_included_projects(self, tmp_path: Path) -> None:
        # Two project manifests, each declaring a private consumer of CxRP.
        # Composed via a WorkScopeManifest, contract impact on CxRP must
        # surface BOTH consumers alongside the public platform consumers.
        proj_a = tmp_path / "a.yaml"
        proj_a.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  proj_a_api:\n'
            '    canonical_name: ProjectAAPI\n'
            '    visibility: private\n'
            'edges:\n'
            '  - {from: ProjectAAPI, to: CxRP, type: depends_on_contracts_from}\n',
            encoding="utf-8",
        )
        proj_b = tmp_path / "b.yaml"
        proj_b.write_text(
            'manifest_kind: project\n'
            'manifest_version: "1.0.0"\n'
            'repos:\n'
            '  proj_b_api:\n'
            '    canonical_name: ProjectBAPI\n'
            '    visibility: private\n'
            'edges:\n'
            '  - {from: ProjectBAPI, to: CxRP, type: depends_on_contracts_from}\n',
            encoding="utf-8",
        )
        ws = tmp_path / "work_scope.yaml"
        ws.write_text(
            'manifest_kind: work_scope\n'
            'manifest_version: "1.0.0"\n'
            'includes:\n'
            f'  - {{name: A, project_manifest_path: {proj_a}}}\n'
            f'  - {{name: B, project_manifest_path: {proj_b}}}\n',
            encoding="utf-8",
        )
        g = build_effective_repo_graph(work_scope_manifest_path=ws)
        summary = compute_contract_impact(g, "CxRP")
        assert summary is not None
        names = {n.canonical_name for n in summary.affected}
        # Public platform consumers PLUS both private include consumers
        assert {"OperationsCenter", "SwitchBoard", "OperatorConsole",
                "ProjectAAPI", "ProjectBAPI"} == names
        assert len(summary.public_affected) == 3
        assert len(summary.private_affected) == 2
        # Privates are both Visibility.PRIVATE
        assert all(n.visibility is Visibility.PRIVATE for n in summary.private_affected)
