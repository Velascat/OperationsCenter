# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the example managed-repo producer profile.

Verifies the profile:
- Is clearly separated from the generic contract.
- Documents all six audit types.
- Captures Phase 0 ground truth.
- Can be reused without altering the generic contract.
"""

from __future__ import annotations


from operations_center.audit_contracts.profiles import EXAMPLE_MANAGED_REPO_PROFILE, ManagedRepoAuditProfile
from operations_center.audit_contracts.vocabulary import (
    GENERIC_ENUMS,
    ExampleManagedRepoAuditType,
)


class TestExampleManagedRepoProfileSeparation:
    def test_producer_id_is_example_managed_repo(self) -> None:
        assert EXAMPLE_MANAGED_REPO_PROFILE.producer_id == "example_managed_repo"

    def test_profile_does_not_import_generic_enums(self) -> None:
        # The profile must not BE a generic enum; it is a producer extension.
        assert not isinstance(EXAMPLE_MANAGED_REPO_PROFILE, tuple(GENERIC_ENUMS))

    def test_another_producer_could_define_own_profile(self) -> None:
        # Demonstrate reuse: a second producer can define a minimal profile
        # without touching the generic contract.
        other = ManagedRepoAuditProfile(
            producer_id="other_repo",
            audit_type_specs=[],
            known_source_stages=[],
            known_artifact_kinds=[],
        )
        assert other.producer_id == "other_repo"
        # Generic contract models are unchanged by this profile
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        assert ManagedRunStatus is not None


class TestExampleManagedRepoAuditTypes:
    def test_all_audit_types_have_specs(self) -> None:
        spec_types = {s.audit_type for s in EXAMPLE_MANAGED_REPO_PROFILE.audit_type_specs}
        expected = {at.value for at in ExampleManagedRepoAuditType}
        assert spec_types == expected

    def test_audit_type_1_has_finalization(self) -> None:
        spec = EXAMPLE_MANAGED_REPO_PROFILE.get_audit_type_spec("audit_type_1")
        assert spec is not None
        assert spec.run_status_finalization is True

    def test_audit_type_2_lacks_finalization(self) -> None:
        non_finalizing = EXAMPLE_MANAGED_REPO_PROFILE.audit_types_without_finalization
        assert non_finalizing == ["audit_type_2"]


class TestExampleManagedRepoEvidence:
    def test_finalized_audit_type_has_phase_0_evidence(self) -> None:
        spec = EXAMPLE_MANAGED_REPO_PROFILE.get_audit_type_spec("audit_type_1")
        assert spec is not None
        assert spec.phase_0_evidence

    def test_unfinalized_audit_type_has_phase_0_evidence(self) -> None:
        spec = EXAMPLE_MANAGED_REPO_PROFILE.get_audit_type_spec("audit_type_2")
        assert spec is not None
        assert spec.phase_0_evidence

    def test_run_status_finalization_gap_documented(self) -> None:
        assert "in_progress" in EXAMPLE_MANAGED_REPO_PROFILE.run_status_finalization_gap

    def test_legacy_status_value_documented(self) -> None:
        assert "in_progress" in EXAMPLE_MANAGED_REPO_PROFILE.legacy_status_value


class TestExampleManagedRepoArchitectureInvariants:
    def test_singleton_path_set(self) -> None:
        assert "latest.json" in EXAMPLE_MANAGED_REPO_PROFILE.architecture_invariants_singleton_path

    def test_singleton_note_mentions_repo_singleton(self) -> None:
        assert "repo_singleton" in EXAMPLE_MANAGED_REPO_PROFILE.architecture_invariants_note


class TestExampleManagedRepoExcludedPathPatterns:
    def test_coverage_ini_excluded(self) -> None:
        patterns = EXAMPLE_MANAGED_REPO_PROFILE.excluded_path_patterns
        assert "coverage.ini" in patterns

    def test_coverage_data_excluded(self) -> None:
        patterns = EXAMPLE_MANAGED_REPO_PROFILE.excluded_path_patterns
        assert any(".coverage" in p for p in patterns)

    def test_sitecustomize_excluded(self) -> None:
        patterns = EXAMPLE_MANAGED_REPO_PROFILE.excluded_path_patterns
        assert "sitecustomize.py" in patterns


class TestManagedRepoPathQuirks:
    def test_path_quirks_documented(self) -> None:
        assert len(EXAMPLE_MANAGED_REPO_PROFILE.path_quirks) >= 2

    def test_non_uniform_layout_quirk_documented(self) -> None:
        quirk_descs = " ".join(q.description for q in EXAMPLE_MANAGED_REPO_PROFILE.path_quirks).lower()
        assert "non-uniform" in quirk_descs or "uniform" in quirk_descs


class TestBoundaryEnforcement:
    def test_no_managed_repo_imports_in_contract_code(self) -> None:
        import ast
        from pathlib import Path
        contract_dir = Path(__file__).parent.parent.parent.parent / "src" / "operations_center" / "audit_contracts"
        for py_file in contract_dir.rglob("*.py"):
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        assert not node.module.startswith("tools.audit"), (
                            f"{py_file}: imports managed-repo code: {node.module}"
                        )
                        assert not node.module.startswith("workflow."), (
                            f"{py_file}: imports managed-repo workflow code: {node.module}"
                        )
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            assert not alias.name.startswith("tools.audit"), (
                                f"{py_file}: imports managed-repo code: {alias.name}"
                            )
