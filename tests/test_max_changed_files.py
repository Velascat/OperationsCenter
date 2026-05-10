# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the max_changed_files scope guard rail.

Covers:
- Candidates within the file limit pass through unchanged.
- Candidates over the limit are suppressed with reason 'scope_too_broad'.
- Candidates with no estimated_affected_files are unaffected.
- The guard fires in DecisionPolicy, not silently.
- distinct_file_count is present in lint/type insight evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime


from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.models import ProposalOutline
from operations_center.decision.policy import DecisionPolicy, DecisionPolicyConfig
from operations_center.insights.derivers.lint_drift import LintDriftDeriver
from operations_center.insights.derivers.type_health import TypeHealthDeriver
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import (
    LintSignal,
    LintViolation,
    RepoContextSnapshot,
    RepoSignalsSnapshot,
    RepoStateSnapshot,
    CheckSignal,
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
        test_signal=CheckSignal(status="unknown"),
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


# ── worsened path carries distinct_file_count ────────────────────────────


def _make_snapshot_with_count(
    *,
    lint_count: int = 0,
    lint_distinct: int = 0,
    lint_violations: list[LintViolation] | None = None,
    type_count: int = 0,
    type_distinct: int = 0,
    type_errors: list[TypeError] | None = None,
) -> RepoStateSnapshot:
    """Build a snapshot with explicit signal counts (for worsened-path testing)."""
    lint = LintSignal(
        status="violations" if lint_count > 0 else "clean",
        violation_count=lint_count,
        distinct_file_count=lint_distinct,
        top_violations=lint_violations or [],
    )
    type_sig = TypeSignal(
        status="errors" if type_count > 0 else "clean",
        error_count=type_count,
        distinct_file_count=type_distinct,
        top_errors=type_errors or [],
    )
    signals = RepoSignalsSnapshot(
        test_signal=CheckSignal(status="unknown"),
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


def test_lint_worsened_insight_includes_distinct_file_count() -> None:
    """LintDriftDeriver worsened insight must carry distinct_file_count."""
    current = _make_snapshot_with_count(lint_count=20, lint_distinct=8)
    prior = _make_snapshot_with_count(lint_count=10, lint_distinct=5)
    normalizer = InsightNormalizer()
    deriver = LintDriftDeriver(normalizer)
    insights = deriver.derive([current, prior])
    worsened = [i for i in insights if i.dedup_key.endswith("worsened")]
    assert len(worsened) == 1
    assert "distinct_file_count" in worsened[0].evidence
    assert worsened[0].evidence["distinct_file_count"] == 8  # from current snapshot


def test_type_worsened_insight_includes_distinct_file_count() -> None:
    """TypeHealthDeriver worsened insight must carry distinct_file_count."""
    current = _make_snapshot_with_count(type_count=15, type_distinct=6)
    prior = _make_snapshot_with_count(type_count=5, type_distinct=3)
    normalizer = InsightNormalizer()
    deriver = TypeHealthDeriver(normalizer)
    insights = deriver.derive([current, prior])
    worsened = [i for i in insights if i.dedup_key.endswith("worsened")]
    assert len(worsened) == 1
    assert "distinct_file_count" in worsened[0].evidence
    assert worsened[0].evidence["distinct_file_count"] == 6


def test_lint_worsened_scope_guard_fires_via_rule_and_policy() -> None:
    """End-to-end: worsened lint insight with broad file scope → scope_too_broad suppression."""
    from operations_center.decision.rules.lint_fix import LintFixRule

    # Build a worsened insight with 35 distinct files (over the default 30 limit)
    current = _make_snapshot_with_count(lint_count=50, lint_distinct=35)
    prior = _make_snapshot_with_count(lint_count=20, lint_distinct=10)
    normalizer = InsightNormalizer()
    deriver = LintDriftDeriver(normalizer)
    insights = deriver.derive([current, prior])

    rule = LintFixRule(min_violations=5)
    specs = rule.evaluate(insights)
    worsened_specs = [s for s in specs if s.pattern_key == "violations_worsened"]
    assert len(worsened_specs) == 1
    assert worsened_specs[0].estimated_affected_files == 35

    policy = _make_policy(max_changed_files=30)
    emitted, suppressed = policy.apply(
        candidate_specs=worsened_specs, prior_artifacts=[], generated_at=_NOW
    )
    assert len(emitted) == 0
    assert suppressed[0].reason == "scope_too_broad"
    assert suppressed[0].evidence["estimated_affected_files"] == 35


def test_type_worsened_scope_guard_fires_via_rule_and_policy() -> None:
    """End-to-end: worsened type insight with broad file scope → scope_too_broad suppression."""
    from operations_center.decision.rules.type_improvement import TypeImprovementRule

    current = _make_snapshot_with_count(type_count=40, type_distinct=32)
    prior = _make_snapshot_with_count(type_count=10, type_distinct=8)
    normalizer = InsightNormalizer()
    deriver = TypeHealthDeriver(normalizer)
    insights = deriver.derive([current, prior])

    rule = TypeImprovementRule(min_errors=3)
    specs = rule.evaluate(insights)
    worsened_specs = [s for s in specs if s.pattern_key == "errors_worsened"]
    assert len(worsened_specs) == 1
    assert worsened_specs[0].estimated_affected_files == 32

    policy = _make_policy(max_changed_files=30)
    emitted, suppressed = policy.apply(
        candidate_specs=worsened_specs, prior_artifacts=[], generated_at=_NOW
    )
    assert len(emitted) == 0
    assert suppressed[0].reason == "scope_too_broad"
    assert suppressed[0].evidence["estimated_affected_files"] == 32


def test_worsened_within_limit_passes_scope_guard() -> None:
    """Worsened candidate under the file limit is not suppressed by scope guard."""
    from operations_center.decision.rules.lint_fix import LintFixRule

    current = _make_snapshot_with_count(lint_count=25, lint_distinct=10)
    prior = _make_snapshot_with_count(lint_count=15, lint_distinct=8)
    normalizer = InsightNormalizer()
    deriver = LintDriftDeriver(normalizer)
    insights = deriver.derive([current, prior])

    rule = LintFixRule(min_violations=5)
    specs = rule.evaluate(insights)
    worsened_specs = [s for s in specs if s.pattern_key == "violations_worsened"]
    assert len(worsened_specs) == 1
    assert worsened_specs[0].estimated_affected_files == 10

    policy = _make_policy(max_changed_files=30)
    emitted, suppressed = policy.apply(
        candidate_specs=worsened_specs, prior_artifacts=[], generated_at=_NOW
    )
    assert len(emitted) == 1
    assert not any(s.reason == "scope_too_broad" for s in suppressed)
