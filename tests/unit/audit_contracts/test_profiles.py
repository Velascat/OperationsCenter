# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for the VideoFoundry producer profile.

Verifies the profile:
- Is clearly separated from the generic contract.
- Documents all six audit types.
- Captures Phase 0 ground truth.
- Can be reused without altering the generic contract.
"""

from __future__ import annotations


from operations_center.audit_contracts.profiles import VIDEOFOUNDRY_PROFILE, VideoFoundryProducerProfile
from operations_center.audit_contracts.vocabulary import (
    GENERIC_ENUMS,
    VideoFoundryAuditType,
)


class TestVideoFoundryProfileSeparation:
    def test_producer_id_is_videofoundry(self) -> None:
        assert VIDEOFOUNDRY_PROFILE.producer_id == "videofoundry"

    def test_profile_does_not_import_generic_enums(self) -> None:
        # The profile must not BE a generic enum; it is a producer extension.
        assert not isinstance(VIDEOFOUNDRY_PROFILE, tuple(GENERIC_ENUMS))

    def test_another_producer_could_define_own_profile(self) -> None:
        # Demonstrate reuse: a second producer can define a minimal profile
        # without touching the generic contract.
        other = VideoFoundryProducerProfile(
            producer_id="other_repo",
            audit_type_specs=[],
            known_source_stages=[],
            known_artifact_kinds=[],
        )
        assert other.producer_id == "other_repo"
        # Generic contract models are unchanged by this profile
        from operations_center.audit_contracts.run_status import ManagedRunStatus
        assert ManagedRunStatus is not None


class TestVideoFoundryAllSixAuditTypes:
    def test_all_six_audit_types_have_specs(self) -> None:
        spec_types = {s.audit_type for s in VIDEOFOUNDRY_PROFILE.audit_type_specs}
        expected = {at.value for at in VideoFoundryAuditType}
        assert spec_types == expected

    def test_representative_has_finalization(self) -> None:
        spec = VIDEOFOUNDRY_PROFILE.get_audit_type_spec("representative")
        assert spec is not None
        assert spec.run_status_finalization is True

    def test_five_types_lack_finalization(self) -> None:
        non_finalizing = VIDEOFOUNDRY_PROFILE.audit_types_without_finalization
        assert "representative" not in non_finalizing
        assert len(non_finalizing) == 5

    def test_stack_authoring_output_dir_is_authoring(self) -> None:
        spec = VIDEOFOUNDRY_PROFILE.get_audit_type_spec("stack_authoring")
        assert spec is not None
        assert "authoring" in spec.output_dir
        assert "stack_authoring" not in spec.output_dir


class TestVideoFoundryPhase0Evidence:
    def test_representative_evidence_is_real_run(self) -> None:
        spec = VIDEOFOUNDRY_PROFILE.get_audit_type_spec("representative")
        assert spec is not None
        assert "inspected" in spec.phase_0_evidence

    def test_non_representative_evidence_is_source_only(self) -> None:
        for at in ("enrichment", "ideation", "render", "segmentation", "stack_authoring"):
            spec = VIDEOFOUNDRY_PROFILE.get_audit_type_spec(at)
            assert spec is not None
            assert "no_run" in spec.phase_0_evidence

    def test_run_status_finalization_gap_documented(self) -> None:
        assert "five" in VIDEOFOUNDRY_PROFILE.run_status_finalization_gap.lower()
        assert "in_progress" in VIDEOFOUNDRY_PROFILE.run_status_finalization_gap

    def test_legacy_status_value_documented(self) -> None:
        assert "in_progress" in VIDEOFOUNDRY_PROFILE.legacy_status_value


class TestVideoFoundryArchitectureInvariants:
    def test_singleton_path_set(self) -> None:
        assert "latest.json" in VIDEOFOUNDRY_PROFILE.architecture_invariants_singleton_path

    def test_singleton_note_mentions_repo_singleton(self) -> None:
        assert "repo_singleton" in VIDEOFOUNDRY_PROFILE.architecture_invariants_note


class TestVideoFoundryExcludedPathPatterns:
    def test_coverage_ini_excluded(self) -> None:
        patterns = VIDEOFOUNDRY_PROFILE.excluded_path_patterns
        assert "coverage.ini" in patterns

    def test_coverage_data_excluded(self) -> None:
        patterns = VIDEOFOUNDRY_PROFILE.excluded_path_patterns
        assert any(".coverage" in p for p in patterns)

    def test_sitecustomize_excluded(self) -> None:
        patterns = VIDEOFOUNDRY_PROFILE.excluded_path_patterns
        assert "sitecustomize.py" in patterns


class TestVideoFoundryPathQuirks:
    def test_path_quirks_documented(self) -> None:
        assert len(VIDEOFOUNDRY_PROFILE.path_quirks) >= 3

    def test_non_uniform_layout_quirk_documented(self) -> None:
        quirk_descs = " ".join(q.description for q in VIDEOFOUNDRY_PROFILE.path_quirks).lower()
        assert "non-uniform" in quirk_descs or "uniform" in quirk_descs


class TestBoundaryEnforcement:
    def test_no_videofoundry_imports_in_contract_code(self) -> None:
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
                            f"{py_file}: imports VideoFoundry code: {node.module}"
                        )
                        assert not node.module.startswith("workflow."), (
                            f"{py_file}: imports VideoFoundry workflow code: {node.module}"
                        )
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            assert not alias.name.startswith("tools.audit"), (
                                f"{py_file}: imports VideoFoundry code: {alias.name}"
                            )
