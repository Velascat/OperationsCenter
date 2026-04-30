# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for validation profile assignment and task body inclusion.

Covers:
- Every named family resolves to the correct profile constant.
- Unknown families fall back to TESTS_PASS without raising.
- CandidateBuilder populates validation_profile from profile_for_family() by default.
- CandidateSpec can override the profile explicitly; the override is respected.
- candidate_mapper includes validation_profile in the task body Provenance block.
- candidate_mapper includes requires_human_approval based on tier + risk_class.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.decision.candidate_builder import CandidateBuilder, CandidateSpec
from operations_center.decision.models import (
    CandidateRationale,
    ProposalCandidate,
    ProposalOutline,
)
from operations_center.decision.validation_profiles import (
    CI_GREEN,
    MANUAL_REVIEW,
    RUFF_CLEAN,
    TESTS_PASS,
    TY_CLEAN,
    profile_for_family,
)
from operations_center.proposer.candidate_mapper import ProposalCandidateMapper
from operations_center.proposer.provenance import ProposalProvenance


# ── profile_for_family ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "family,expected",
    [
        ("lint_fix", RUFF_CLEAN),
        ("type_fix", TY_CLEAN),
        ("test_visibility", TESTS_PASS),
        ("execution_health_followup", TESTS_PASS),
        ("observation_coverage", TESTS_PASS),
        ("dependency_drift_followup", TESTS_PASS),
        ("ci_pattern", CI_GREEN),
        ("validation_pattern_followup", TESTS_PASS),
        ("hotspot_concentration", TESTS_PASS),
        ("todo_accumulation", TESTS_PASS),
        ("backlog_promotion", TESTS_PASS),
        ("arch_promotion", MANUAL_REVIEW),
    ],
)
def test_profile_for_known_family(family: str, expected: str) -> None:
    assert profile_for_family(family) == expected


def test_profile_for_unknown_family_falls_back_to_tests_pass() -> None:
    assert profile_for_family("some_future_family") == TESTS_PASS


# ── CandidateBuilder ─────────────────────────────────────────────────────────


def _spec(family: str, validation_profile: str = "") -> CandidateSpec:
    return CandidateSpec(
        family=family,
        subject="test",
        pattern_key="present",
        evidence={},
        matched_rules=["test"],
        confidence="high",
        risk_class="style",
        expires_after_runs=3,
        validation_profile=validation_profile,
        proposal_outline=ProposalOutline(
            title_hint=f"Fix {family}",
            summary_hint=f"Fix {family} issues.",
        ),
        priority=(1, 1, family),
    )


def test_builder_auto_assigns_profile_from_family() -> None:
    builder = CandidateBuilder()
    candidate = builder.build(_spec("lint_fix"))
    assert candidate.validation_profile == RUFF_CLEAN


def test_builder_auto_assigns_profile_type_fix() -> None:
    builder = CandidateBuilder()
    candidate = builder.build(_spec("type_fix"))
    assert candidate.validation_profile == TY_CLEAN


def test_builder_respects_explicit_override() -> None:
    """A rule can explicitly set validation_profile; the builder must not override it."""
    builder = CandidateBuilder()
    candidate = builder.build(_spec("lint_fix", validation_profile=TESTS_PASS))
    assert candidate.validation_profile == TESTS_PASS


def test_builder_unknown_family_gets_tests_pass() -> None:
    builder = CandidateBuilder()
    candidate = builder.build(_spec("brand_new_family"))
    assert candidate.validation_profile == TESTS_PASS


# ── candidate_mapper task body ───────────────────────────────────────────────


def _candidate(
    family: str = "lint_fix",
    risk_class: str = "style",
    validation_profile: str = RUFF_CLEAN,
) -> ProposalCandidate:
    return ProposalCandidate(
        candidate_id="cand:test",
        dedup_key="candidate|test|subject|present",
        family=family,
        subject="subject",
        status="emit",
        confidence="high",
        risk_class=risk_class,
        expires_after_runs=4,
        validation_profile=validation_profile,
        rationale=CandidateRationale(),
        proposal_outline=ProposalOutline(
            title_hint="Fix lint",
            summary_hint="Fix lint violations.",
        ),
        evidence_lines=["47 violations detected"],
    )


def _provenance(repo_name: str = "OperationsCenter") -> ProposalProvenance:
    return ProposalProvenance(
        source="autonomy",
        source_family="lint_fix",
        candidate_id="cand:test",
        candidate_dedup_key="candidate|test|subject|present",
        repo_name=repo_name,
        observer_run_ids=["obs_001"],
        insight_run_id="ins_001",
        decision_run_id="dec_001",
        proposer_run_id="prop_001",
    )


def _make_settings(tmp_path: Path) -> object:
    from operations_center.config.settings import load_settings
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "\n".join([
            "plane:",
            "  base_url: http://plane.local",
            "  api_token_env: PLANE_API_TOKEN",
            "  workspace_slug: ws",
            "  project_id: proj",
            "git: {}",
            "kodo: {}",
            "repos:",
            "  OperationsCenter:",
            "    clone_url: git@github.com:test/repo.git",
            "    default_branch: main",
        ])
    )
    return load_settings(cfg)


def _map(candidate: ProposalCandidate, tmp_path: Path) -> str:
    settings = _make_settings(tmp_path)
    mapper = ProposalCandidateMapper()
    draft = mapper.map_to_task(
        candidate=candidate,
        settings=settings,
        provenance=_provenance(),
    )
    return draft.description


def test_task_body_includes_validation_profile(tmp_path: Path) -> None:
    body = _map(_candidate(family="lint_fix", validation_profile=RUFF_CLEAN), tmp_path)
    assert "validation_profile: ruff_clean" in body


def test_task_body_includes_ty_clean_for_type_fix(tmp_path: Path) -> None:
    body = _map(_candidate(family="type_fix", risk_class="style", validation_profile=TY_CLEAN), tmp_path)
    assert "validation_profile: ty_clean" in body


def test_task_body_requires_human_approval_false_for_tier2_style(tmp_path: Path) -> None:
    """lint_fix at tier 2 (style) → Ready for AI → requires_human_approval: false."""
    body = _map(_candidate(family="lint_fix", risk_class="style"), tmp_path)
    assert "requires_human_approval: false" in body


def test_task_body_requires_human_approval_true_for_logic(tmp_path: Path) -> None:
    """execution_health_followup at tier 1 with logic risk → Backlog → requires_human_approval: true."""
    body = _map(
        _candidate(
            family="execution_health_followup",
            risk_class="logic",
            validation_profile=TESTS_PASS,
        ),
        tmp_path,
    )
    assert "requires_human_approval: true" in body


# ── EvidenceBundle synthesis ─────────────────────────────────────────────────


def _lint_spec(pattern_key: str = "violations_present", **evidence_overrides: object) -> CandidateSpec:
    ev = {"violation_count": 47, "distinct_file_count": 5, "top_codes": ["E501"], "source": "ruff"}
    ev.update(evidence_overrides)
    return CandidateSpec(
        family="lint_fix",
        subject="lint_violations",
        pattern_key=pattern_key,
        evidence=ev,
        matched_rules=["test"],
        confidence="high",
        risk_class="style",
        expires_after_runs=3,
        proposal_outline=ProposalOutline(title_hint="Fix lint", summary_hint="Fix lint."),
        priority=(1, 1, "lint_fix"),
    )


def _type_spec(pattern_key: str = "errors_present", **evidence_overrides: object) -> CandidateSpec:
    ev = {"error_count": 12, "distinct_file_count": 3, "top_codes": ["attr-defined"], "source": "mypy"}
    ev.update(evidence_overrides)
    return CandidateSpec(
        family="type_fix",
        subject="type_errors",
        pattern_key=pattern_key,
        evidence=ev,
        matched_rules=["test"],
        confidence="medium",
        risk_class="logic",
        expires_after_runs=4,
        proposal_outline=ProposalOutline(title_hint="Fix types", summary_hint="Fix types."),
        priority=(1, 1, "type_fix"),
    )


def test_lint_fix_present_bundle_populated() -> None:
    builder = CandidateBuilder()
    candidate = builder.build(_lint_spec())
    b = candidate.evidence_bundle
    assert b is not None
    assert b.kind == "lint_count"
    assert b.count == 47
    assert b.distinct_file_count == 5
    assert b.delta is None
    assert b.trend == "present"
    assert "E501" in b.top_codes
    assert b.source == "ruff"


def test_lint_fix_worsened_bundle_has_delta() -> None:
    builder = CandidateBuilder()
    ev = {"current_count": 47, "previous_count": 32, "delta": 15}
    candidate = builder.build(_lint_spec(pattern_key="violations_worsened", **ev))
    b = candidate.evidence_bundle
    assert b is not None
    assert b.count == 47
    assert b.delta == 15
    assert b.trend == "worsening"


def test_type_fix_present_bundle_populated() -> None:
    builder = CandidateBuilder()
    candidate = builder.build(_type_spec())
    b = candidate.evidence_bundle
    assert b is not None
    assert b.kind == "type_count"
    assert b.count == 12
    assert b.distinct_file_count == 3
    assert b.source == "mypy"


def test_type_fix_worsened_bundle_has_delta() -> None:
    builder = CandidateBuilder()
    ev = {"current_count": 12, "previous_count": 7, "delta": 5}
    candidate = builder.build(_type_spec(pattern_key="errors_worsened", **ev))
    b = candidate.evidence_bundle
    assert b is not None
    assert b.delta == 5
    assert b.trend == "worsening"


def test_unrelated_family_has_no_bundle() -> None:
    spec = CandidateSpec(
        family="observation_coverage",
        subject="test",
        pattern_key="gap",
        evidence={"reason": "no_tests"},
        matched_rules=["test"],
        confidence="medium",
        risk_class="logic",
        expires_after_runs=5,
        proposal_outline=ProposalOutline(title_hint="Cover", summary_hint="Cover."),
        priority=(1, 1, "coverage"),
    )
    builder = CandidateBuilder()
    candidate = builder.build(spec)
    assert candidate.evidence_bundle is None


def test_bundle_schema_version_is_1() -> None:
    builder = CandidateBuilder()
    candidate = builder.build(_lint_spec())
    assert candidate.evidence_bundle is not None
    assert candidate.evidence_bundle.schema_version == 1


def test_task_body_provenance_fields_ordered(tmp_path: Path) -> None:
    """validation_profile and requires_human_approval appear after risk_class."""
    body = _map(_candidate(), tmp_path)
    lines = body.splitlines()
    rc_idx = next(i for i, line in enumerate(lines) if line.startswith("risk_class:"))
    vp_idx = next(i for i, line in enumerate(lines) if line.startswith("validation_profile:"))
    rha_idx = next(i for i, line in enumerate(lines) if line.startswith("requires_human_approval:"))
    assert rc_idx < vp_idx < rha_idx


def test_task_body_includes_evidence_schema_version(tmp_path: Path) -> None:
    """evidence_schema_version appears in the provenance block."""
    body = _map(_candidate(family="lint_fix"), tmp_path)
    assert "evidence_schema_version: 1" in body


def test_task_body_evidence_schema_version_default_when_no_bundle(tmp_path: Path) -> None:
    """Families with no EvidenceBundle still emit evidence_schema_version: 1."""
    body = _map(
        _candidate(family="observation_coverage", risk_class="logic", validation_profile=TESTS_PASS),
        tmp_path,
    )
    assert "evidence_schema_version: 1" in body
