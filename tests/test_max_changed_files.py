"""Tests for the max_changed_files scope guard rail.

Covers:
- Candidates within the file limit pass through unchanged.
- Candidates over the limit are suppressed with reason 'scope_too_broad'.
- Candidates with no estimated_affected_files are unaffected.
- The guard fires in DecisionPolicy, not silently.
- distinct_file_count is present in lint/type insight evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from control_plane.decision.candidate_builder import CandidateBuilder, CandidateSpec
from control_plane.decision.models import ProposalOutline
from control_plane.decision.policy import DecisionPolicy, DecisionPolicyConfig
from control_plane.insights.derivers.lint_drift import LintDriftDeriver
from control_plane.insights.derivers.type_health import TypeHealthDeriver
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.observer.models import (
    LintSignal,
    LintViolation,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    TestSignal,
    DependencyDriftSignal,
    TodoSignal,
    TypeSignal,
    TypeError,
)


_NOW = datetime(2026, 4, 5, 12, tzinfo=UTC)


def _make_spec(
    *,
    family: str = "lint_fix",
    estimated_affected_files: int | None = None,
) -> CandidateSpec:
    return CandidateSpec(
        family=family,
        subject="lint_violations",
        pattern_key="violations_present",
        evidence={},
        matched_rules=["test"],
        confidence="high",
        risk_class="style",
        expires_after_runs=3,
        estimated_affected_files=estimated_affected_files,
        proposal_outline=ProposalOutline(
            title_hint="Fix lint",
            summary_hint="Fix lint violations.",
        ),
        priority=(1, 1, "test"),
    )


def _make_policy(max_changed_files: int = 30) -> DecisionPolicy:
    return DecisionPolicy(
        config=DecisionPolicyConfig(
            max_candidates=10,
            max_candidates_per_family=5,
            cooldown_minutes=0,
            max_changed_files=max_changed_files,
        )
    )


# ── scope guard: pass / block / ignore ─────────────────────────────────────


def test_within_limit_passes() -> None:
    policy = _make_policy(max_changed_files=10)
    spec = _make_spec(estimated_affected_files=8)
    emitted, suppressed = policy.apply(
        candidate_specs=[spec], prior_artifacts=[], generated_at=_NOW
    )
    assert len(emitted) == 1
    assert len(suppressed) == 0


def test_at_limit_passes() -> None:
    policy = _make_policy(max_changed_files=10)
    spec = _make_spec(estimated_affected_files=10)
    emitted, suppressed = policy.apply(
        candidate_specs=[spec], prior_artifacts=[], generated_at=_NOW
    )
    assert len(emitted) == 1


def test_over_limit_is_suppressed() -> None:
    policy = _make_policy(max_changed_files=10)
    spec = _make_spec(estimated_affected_files=11)
    emitted, suppressed = policy.apply(
        candidate_specs=[spec], prior_artifacts=[], generated_at=_NOW
    )
    assert len(emitted) == 0
    assert len(suppressed) == 1
    s = suppressed[0]
    assert s.reason == "scope_too_broad"
    assert s.evidence["estimated_affected_files"] == 11
    assert s.evidence["max_changed_files"] == 10


def test_none_estimated_files_is_not_suppressed() -> None:
    """Candidates without file count estimate are never suppressed by this guard."""
    policy = _make_policy(max_changed_files=5)
    spec = _make_spec(estimated_affected_files=None)
    emitted, suppressed = policy.apply(
        candidate_specs=[spec], prior_artifacts=[], generated_at=_NOW
    )
    assert len(emitted) == 1
    assert not any(s.reason == "scope_too_broad" for s in suppressed)


def test_scope_suppression_recorded_in_artifact() -> None:
    """Suppression evidence must include both the estimate and the limit."""
    policy = _make_policy(max_changed_files=3)
    spec = _make_spec(estimated_affected_files=15)
    _, suppressed = policy.apply(
        candidate_specs=[spec], prior_artifacts=[], generated_at=_NOW
    )
    ev = suppressed[0].evidence
    assert "estimated_affected_files" in ev
    assert "max_changed_files" in ev


# ── distinct_file_count in deriver evidence ───────────────────────────────


def _make_snapshot(
    *,
    lint_violations: list[LintViolation] | None = None,
    type_errors: list[TypeError] | None = None,
) -> RepoStateSnapshot:
    lint = LintSignal(
        status="violations" if lint_violations else "clean",
        violation_count=len(lint_violations or []),
        top_violations=lint_violations or [],
    )
    type_sig = TypeSignal(
        status="errors" if type_errors else "clean",
        error_count=len(type_errors or []),
        top_errors=type_errors or [],
    )
    signals = RepoSignalsSnapshot(
        test_signal=TestSignal(status="unknown"),
        dependency_drift=DependencyDriftSignal(status="unknown"),
        todo_signal=TodoSignal(),
        lint_signal=lint,
        type_signal=type_sig,
    )
    return RepoStateSnapshot(
        run_id="obs_test",
        observed_at=_NOW,
        source_command="test",
        repo=RepoContextSnapshot(
            name="repo",
            path=__import__("pathlib").Path("/tmp/repo"),
            current_branch="main",
            is_dirty=False,
        ),
        signals=signals,
    )


def _viol(path: str) -> LintViolation:
    return LintViolation(path=path, line=1, col=1, code="E501", message="line too long")


def _terr(path: str) -> TypeError:
    return TypeError(path=path, line=1, col=1, code="attr-defined", message="unknown attr")


def test_lint_deriver_includes_distinct_file_count() -> None:
    violations = [_viol("src/a.py"), _viol("src/b.py"), _viol("src/a.py")]  # a.py twice
    snapshot = _make_snapshot(lint_violations=violations)
    normalizer = InsightNormalizer()
    deriver = LintDriftDeriver(normalizer)
    insights = deriver.derive([snapshot])
    present = [i for i in insights if i.dedup_key.endswith("present")]
    assert len(present) == 1
    assert present[0].evidence["distinct_file_count"] == 2  # a.py + b.py


def test_type_deriver_includes_distinct_file_count() -> None:
    errors = [_terr("src/x.py"), _terr("src/y.py"), _terr("src/x.py")]
    snapshot = _make_snapshot(type_errors=errors)
    normalizer = InsightNormalizer()
    deriver = TypeHealthDeriver(normalizer)
    insights = deriver.derive([snapshot])
    present = [i for i in insights if i.dedup_key.endswith("present")]
    assert len(present) == 1
    assert present[0].evidence["distinct_file_count"] == 2
