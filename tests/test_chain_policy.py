# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for chain-aware candidate sequencing in the decision layer.

Covers:
- Upstream family in same cycle suppresses downstream with 'upstream_family_in_cycle'.
- Upstream family recently emitted suppresses downstream with 'upstream_family_recently_emitted'.
- Unrelated families are completely unaffected.
- Upstream within cooldown window is detected; outside window is not.
- Suppression evidence names the specific upstream and downstream families.
- When upstream is absent, downstream candidate passes through.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from operations_center.decision.candidate_builder import CandidateSpec
from operations_center.decision.chain_policy import ChainPolicy
from operations_center.decision.models import (
    DecisionRepoRef,
    ProposalCandidatesArtifact,
    ProposalCandidate,
    CandidateRationale,
    ProposalOutline,
)

_NOW = datetime(2026, 4, 5, 12, tzinfo=UTC)


def _spec(family: str) -> CandidateSpec:
    return CandidateSpec(
        family=family,
        subject="test",
        pattern_key="present",
        evidence={},
        matched_rules=["test"],
        confidence="high",
        risk_class="style",
        expires_after_runs=3,
        proposal_outline=ProposalOutline(
            title_hint=f"Fix {family}",
            summary_hint=f"Fix {family} issues.",
        ),
        priority=(1, 1, family),
    )


def _prior_artifact(
    *,
    families: list[str],
    generated_at: datetime,
) -> ProposalCandidatesArtifact:
    candidates = [
        ProposalCandidate(
            candidate_id=f"cand:{fam}",
            dedup_key=f"candidate|{fam}|test|present",
            family=fam,
            subject="test",
            status="emit",
            confidence="high",
            risk_class="style",
            expires_after_runs=3,
            rationale=CandidateRationale(),
            proposal_outline=ProposalOutline(title_hint=fam, summary_hint=fam),
        )
        for fam in families
    ]
    return ProposalCandidatesArtifact(
        run_id="dec_prior",
        generated_at=generated_at,
        source_command="test",
        dry_run=False,
        repo=DecisionRepoRef(name="repo", path=Path("/tmp/repo")),
        source_insight_run_id="ins_prior",
        candidates=candidates,
    )


def _policy(cooldown_minutes: int = 120) -> ChainPolicy:
    return ChainPolicy(cooldown_minutes=cooldown_minutes)


# ── in-cycle suppression ─────────────────────────────────────────────────


def test_type_fix_suppressed_when_lint_fix_in_same_cycle() -> None:
    policy = _policy()
    specs = [_spec("lint_fix"), _spec("type_fix")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    live_families = {s.family for s in live}
    assert "lint_fix" in live_families
    assert "type_fix" not in live_families
    assert len(suppressed) == 1
    s = suppressed[0]
    assert s.reason == "upstream_family_in_cycle"
    assert s.evidence["upstream_family"] == "lint_fix"
    assert s.evidence["downstream_family"] == "type_fix"


def test_execution_health_suppressed_when_test_visibility_in_same_cycle() -> None:
    policy = _policy()
    specs = [_spec("test_visibility"), _spec("execution_health_followup")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    live_families = {s.family for s in live}
    assert "test_visibility" in live_families
    assert "execution_health_followup" not in live_families
    assert suppressed[0].reason == "upstream_family_in_cycle"


def test_only_downstream_is_suppressed_not_upstream() -> None:
    policy = _policy()
    specs = [_spec("lint_fix"), _spec("type_fix"), _spec("observation_coverage")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    live_families = {s.family for s in live}
    assert "lint_fix" in live_families
    assert "observation_coverage" in live_families
    assert "type_fix" not in live_families
    assert len(suppressed) == 1


# ── cross-cycle suppression ──────────────────────────────────────────────


def test_downstream_suppressed_when_upstream_recently_emitted() -> None:
    policy = _policy(cooldown_minutes=120)
    # upstream emitted 30 minutes ago (within cooldown)
    recent_artifact = _prior_artifact(
        families=["lint_fix"],
        generated_at=_NOW - timedelta(minutes=30),
    )
    # only type_fix in current cycle (lint_fix not present)
    specs = [_spec("type_fix")]
    live, suppressed = policy.apply(
        specs=specs, prior_artifacts=[recent_artifact], generated_at=_NOW
    )
    assert len(live) == 0
    assert suppressed[0].reason == "upstream_family_recently_emitted"
    assert suppressed[0].evidence["upstream_family"] == "lint_fix"


def test_downstream_passes_when_upstream_outside_cooldown() -> None:
    policy = _policy(cooldown_minutes=120)
    # upstream emitted 3 hours ago (outside cooldown)
    old_artifact = _prior_artifact(
        families=["lint_fix"],
        generated_at=_NOW - timedelta(hours=3),
    )
    specs = [_spec("type_fix")]
    live, suppressed = policy.apply(
        specs=specs, prior_artifacts=[old_artifact], generated_at=_NOW
    )
    assert len(live) == 1
    assert not any(s.reason.startswith("upstream_family") for s in suppressed)


# ── unrelated families unaffected ────────────────────────────────────────


def test_unrelated_family_always_passes() -> None:
    policy = _policy()
    specs = [_spec("lint_fix"), _spec("observation_coverage"), _spec("dependency_drift")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    live_families = {s.family for s in live}
    assert "observation_coverage" in live_families
    assert "dependency_drift" in live_families
    assert not any(
        s.evidence.get("downstream_family") in {"observation_coverage", "dependency_drift"}
        for s in suppressed
    )


def test_no_upstream_present_downstream_passes() -> None:
    """type_fix alone (no lint_fix anywhere) should emit normally."""
    policy = _policy()
    specs = [_spec("type_fix")]
    live, suppressed = policy.apply(specs=specs, prior_artifacts=[], generated_at=_NOW)
    assert len(live) == 1
    assert len(suppressed) == 0


def test_empty_specs_returns_empty() -> None:
    policy = _policy()
    live, suppressed = policy.apply(specs=[], prior_artifacts=[], generated_at=_NOW)
    assert live == []
    assert suppressed == []
