# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for the anti-collapse invariant.

Verifies:
- The one-way promotion pipeline is structurally enforced
- Runtime modules do not import behavior_calibration
- Recommendations are advisory-only (no mutation fields, human review required)
- CalibrationDecision is the only path between recommendations and action
- No forbidden functions exist in the codebase
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from operations_center.behavior_calibration import (
    AnalysisProfile,
    BehaviorCalibrationInput,
    CalibrationDecision,
    CalibrationFinding,
    CalibrationRecommendation,
    FindingCategory,
    FindingSeverity,
    GuardrailViolation,
    RecommendationPriority,
    analyze_artifacts,
    assert_no_mutation_fields,
    enforce_requires_human_review,
    validate_all_recommendations,
    validate_recommendation_structure,
)

_SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "operations_center"
_CALIBRATION_PKG = "operations_center.behavior_calibration"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _imports_calibration(py_file: Path) -> list[str]:
    """Return import statements in py_file that reference behavior_calibration."""
    source = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return []
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "behavior_calibration" in node.module:
                hits.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "behavior_calibration" in alias.name:
                    hits.append(alias.name)
    return hits


def _all_py_files(pkg_subdir: str) -> list[Path]:
    return list((_SRC_ROOT / pkg_subdir).glob("*.py"))


def _make_finding(
    severity: FindingSeverity = FindingSeverity.WARNING,
    category: FindingCategory = FindingCategory.FAILED_RUN,
) -> CalibrationFinding:
    return CalibrationFinding(
        severity=severity,
        category=category,
        summary="test finding",
        source="test",
    )


def _make_recommendation(
    *,
    finding_ids: list[str] | None = None,
    requires_human_review: bool = True,
) -> CalibrationRecommendation:
    f = _make_finding()
    return CalibrationRecommendation(
        priority=RecommendationPriority.MEDIUM,
        summary="do something",
        rationale="because test",
        affected_repo_id="videofoundry",
        affected_audit_type="representative",
        suggested_action="check logs",
        requires_human_review=requires_human_review,
        supporting_finding_ids=finding_ids if finding_ids is not None else [f.finding_id],
    )


def make_input(index, profile: AnalysisProfile, **kwargs) -> BehaviorCalibrationInput:
    return BehaviorCalibrationInput(
        repo_id=index.source.repo_id,
        run_id=index.source.run_id,
        audit_type=index.source.audit_type,
        artifact_index=index,
        analysis_profile=profile,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Rule 1 — Directionality: runtime modules must not import behavior_calibration
# ---------------------------------------------------------------------------

class TestImportBoundary:
    """AST-level enforcement: runtime packages must not depend on behavior_calibration."""

    @pytest.mark.parametrize("pkg_subdir", [
        "audit_dispatch",
        "run_identity",
        "managed_repos",
        "config",
        "routing",
        "planning",
        "policy",
        "execution",
        "observability",
    ])
    def test_runtime_module_does_not_import_calibration(self, pkg_subdir: str) -> None:
        pkg_path = _SRC_ROOT / pkg_subdir
        if not pkg_path.exists():
            pytest.skip(f"Package {pkg_subdir!r} not present in this checkout")
        for py_file in pkg_path.glob("*.py"):
            hits = _imports_calibration(py_file)
            assert hits == [], (
                f"{pkg_subdir}/{py_file.name} imports behavior_calibration: {hits}. "
                "Runtime modules must not depend on calibration output."
            )

    def test_behavior_calibration_may_import_artifact_index(self) -> None:
        calibration_dir = _SRC_ROOT / "behavior_calibration"
        imports_artifact_index = False
        for py_file in calibration_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            if "artifact_index" in source:
                imports_artifact_index = True
                break
        assert imports_artifact_index, (
            "behavior_calibration should use artifact_index (Phase 7); none found"
        )

    def test_artifact_index_does_not_import_calibration(self) -> None:
        for py_file in _all_py_files("artifact_index"):
            hits = _imports_calibration(py_file)
            assert hits == [], (
                f"artifact_index/{py_file.name} must not import behavior_calibration: {hits}"
            )


# ---------------------------------------------------------------------------
# Rule 2 — Findings Are Facts
# ---------------------------------------------------------------------------

class TestFindingsAreFacts:
    def test_finding_references_artifact_ids(self) -> None:
        f = CalibrationFinding(
            severity=FindingSeverity.ERROR,
            category=FindingCategory.MISSING_FILE,
            summary="file missing",
            source="check_missing_files",
            artifact_ids=["videofoundry:representative:SomeStage:artifact"],
        )
        assert len(f.artifact_ids) == 1

    def test_finding_is_immutable(self) -> None:
        f = _make_finding()
        with pytest.raises(Exception):
            f.summary = "mutated"  # type: ignore[misc]

    def test_finding_has_no_apply_method(self) -> None:
        f = _make_finding()
        assert not hasattr(f, "apply")
        assert not hasattr(f, "execute")
        assert not hasattr(f, "mutate")

    def test_finding_has_no_config_field(self) -> None:
        f = _make_finding()
        data = f.model_dump()
        forbidden = {"config_patch", "auto_apply", "execute", "runtime_patch"}
        assert not (forbidden & set(data.keys()))

    def test_finding_severity_is_descriptive_not_imperative(self) -> None:
        # Severity values describe state, not commands
        for severity in FindingSeverity:
            assert severity.value in ("info", "warning", "error", "critical")


# ---------------------------------------------------------------------------
# Rule 3 — Recommendations Are Advisory
# ---------------------------------------------------------------------------

class TestRecommendationsAreAdvisory:
    def test_requires_human_review_always_true(self) -> None:
        rec = _make_recommendation()
        assert rec.requires_human_review is True

    def test_requires_human_review_cannot_be_false(self) -> None:
        # Pydantic allows construction with False, but guardrail must catch it
        rec = _make_recommendation(requires_human_review=False)
        with pytest.raises(GuardrailViolation, match="requires_human_review"):
            enforce_requires_human_review(rec)

    def test_recommendation_is_immutable(self) -> None:
        rec = _make_recommendation()
        with pytest.raises(Exception):
            rec.summary = "mutated"  # type: ignore[misc]

    def test_recommendation_has_no_auto_apply_field(self) -> None:
        rec = _make_recommendation()
        assert not hasattr(rec, "auto_apply")
        assert not hasattr(rec, "apply_immediately")
        assert not hasattr(rec, "execute")

    def test_recommendation_has_no_mutation_fields(self) -> None:
        rec = _make_recommendation()
        assert_no_mutation_fields(rec)  # must not raise

    def test_recommendation_requires_supporting_findings(self) -> None:
        rec = _make_recommendation(finding_ids=[])
        with pytest.raises(GuardrailViolation, match="supporting_finding_ids"):
            validate_recommendation_structure(rec)

    def test_validate_recommendation_structure_passes_valid(self) -> None:
        rec = _make_recommendation()
        validate_recommendation_structure(rec)  # must not raise

    def test_recommendation_has_risk_field(self) -> None:
        rec = _make_recommendation()
        assert rec.risk in ("low", "medium", "high")

    def test_recommendation_has_suggested_action_not_command(self) -> None:
        rec = _make_recommendation()
        # suggested_action should exist; no callable/executable field
        assert isinstance(rec.suggested_action, str)
        assert not callable(rec.suggested_action)


# ---------------------------------------------------------------------------
# Rule 4 — Promotion Barrier: CalibrationDecision
# ---------------------------------------------------------------------------

class TestCalibrationDecision:
    def test_decision_requires_approved_by(self) -> None:
        rec = _make_recommendation()
        decision = CalibrationDecision(
            source_recommendation_ids=[rec.recommendation_id],
            approved_by="alice",
        )
        assert decision.approved_by == "alice"

    def test_decision_links_recommendation_ids(self) -> None:
        rec = _make_recommendation()
        decision = CalibrationDecision(
            source_recommendation_ids=[rec.recommendation_id],
            approved_by="bob",
        )
        assert rec.recommendation_id in decision.source_recommendation_ids

    def test_decision_is_frozen(self) -> None:
        decision = CalibrationDecision(
            source_recommendation_ids=["rec-1"],
            approved_by="carol",
        )
        with pytest.raises(Exception):
            decision.approved_by = "mallory"  # type: ignore[misc]

    def test_decision_has_auto_id(self) -> None:
        decision = CalibrationDecision(
            source_recommendation_ids=["rec-1"],
            approved_by="dave",
        )
        assert len(decision.decision_id) > 0

    def test_decision_is_applied_when_reference_set(self) -> None:
        decision = CalibrationDecision(
            source_recommendation_ids=["rec-1"],
            approved_by="eve",
            applied_changes_reference="https://github.com/org/repo/pull/42",
        )
        assert decision.is_applied is True

    def test_decision_not_applied_by_default(self) -> None:
        decision = CalibrationDecision(
            source_recommendation_ids=["rec-1"],
            approved_by="frank",
        )
        assert decision.is_applied is False

    def test_decision_has_no_execute_method(self) -> None:
        decision = CalibrationDecision(
            source_recommendation_ids=["rec-1"],
            approved_by="grace",
        )
        assert not hasattr(decision, "execute")
        assert not hasattr(decision, "apply")
        assert not hasattr(decision, "run")

    def test_decision_serializes_to_json(self) -> None:
        import json
        decision = CalibrationDecision(
            source_recommendation_ids=["rec-1"],
            approved_by="heidi",
            decision_notes="approved after review",
        )
        data = json.loads(decision.model_dump_json())
        assert data["approved_by"] == "heidi"
        assert data["source_recommendation_ids"] == ["rec-1"]


# ---------------------------------------------------------------------------
# Rule 5 — No Auto-Apply: forbidden function names must not exist
# ---------------------------------------------------------------------------

class TestNoAutoApply:
    _FORBIDDEN_FUNCTION_NAMES = frozenset({
        "auto_apply_recommendations",
        "self_tuning_runtime",
        "apply_recommendation",
        "execute_recommendation",
        "auto_tune",
    })

    def test_no_forbidden_functions_in_calibration_package(self) -> None:
        calibration_dir = _SRC_ROOT / "behavior_calibration"
        for py_file in calibration_dir.glob("*.py"):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    assert node.name not in self._FORBIDDEN_FUNCTION_NAMES, (
                        f"{py_file.name} defines forbidden function {node.name!r}. "
                        "Auto-apply functions are not permitted in the calibration package."
                    )


# ---------------------------------------------------------------------------
# Rule 6 — validate_all_recommendations integration
# ---------------------------------------------------------------------------

class TestValidateAllRecommendations:
    def test_valid_list_passes(self) -> None:
        recs = [_make_recommendation() for _ in range(3)]
        validate_all_recommendations(recs)  # must not raise

    def test_empty_list_passes(self) -> None:
        validate_all_recommendations([])  # must not raise

    def test_one_invalid_raises(self) -> None:
        valid = _make_recommendation()
        invalid = _make_recommendation(finding_ids=[])
        with pytest.raises(GuardrailViolation):
            validate_all_recommendations([valid, invalid])

    def test_analyzer_output_passes_guardrails(self, failed_index) -> None:
        report = analyze_artifacts(make_input(failed_index, AnalysisProfile.RECOMMENDATION))
        validate_all_recommendations(report.recommendations)

    def test_summary_profile_has_no_recommendations_to_validate(self, completed_index) -> None:
        report = analyze_artifacts(make_input(completed_index, AnalysisProfile.SUMMARY))
        validate_all_recommendations(report.recommendations)  # empty list, must not raise


# ---------------------------------------------------------------------------
# Rule 7 — Schema Separation: no shared mutable model
# ---------------------------------------------------------------------------

class TestSchemaSeparation:
    def test_finding_and_recommendation_are_distinct_types(self) -> None:
        f = _make_finding()
        r = _make_recommendation()
        assert type(f) is not type(r)

    def test_decision_is_distinct_from_recommendation(self) -> None:
        r = _make_recommendation()
        d = CalibrationDecision(
            source_recommendation_ids=[r.recommendation_id],
            approved_by="ivan",
        )
        assert type(d) is not type(r)

    def test_finding_cannot_be_used_as_recommendation(self) -> None:
        f = _make_finding()
        assert not isinstance(f, CalibrationRecommendation)

    def test_recommendation_cannot_be_used_as_decision(self) -> None:
        r = _make_recommendation()
        assert not isinstance(r, CalibrationDecision)
