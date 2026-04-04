from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from control_plane.decision.candidate_builder import CandidateBuilder, CandidateSpec
from control_plane.decision.loader import DecisionLoader
from control_plane.decision.models import ProposalCandidatesArtifact, ProposalOutline
from control_plane.decision.policy import DecisionPolicy, DecisionPolicyConfig
from control_plane.decision.service import DecisionEngineService, new_decision_context
from control_plane.execution import UsageStore
from control_plane.insights.artifact_writer import InsightArtifactWriter
from control_plane.insights.models import DerivedInsight, InsightRepoRef, RepoInsightsArtifact, SourceSnapshotRef


def make_insight_artifact(
    *,
    run_id: str,
    generated_at: datetime,
    repo_path: Path,
    insights: list[DerivedInsight],
) -> RepoInsightsArtifact:
    return RepoInsightsArtifact(
        run_id=run_id,
        generated_at=generated_at,
        source_command="control-plane generate-insights",
        repo=InsightRepoRef(name="control-plane", path=repo_path),
        source_snapshots=[SourceSnapshotRef(run_id="obs_1", observed_at=generated_at - timedelta(hours=1))],
        insights=insights,
    )


def make_insight(
    *,
    kind: str,
    subject: str,
    dedup_key: str,
    evidence: dict[str, object],
) -> DerivedInsight:
    ts = datetime(2026, 3, 31, 12, tzinfo=UTC)
    return DerivedInsight(
        insight_id=dedup_key.replace("|", ":"),
        dedup_key=dedup_key,
        kind=kind,
        subject=subject,
        status="present",
        evidence=evidence,
        first_seen_at=ts - timedelta(hours=1),
        last_seen_at=ts,
    )


def test_loader_reads_latest_insight_and_bounded_decision_history(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    insights_root = tmp_path / "insights"
    writer = InsightArtifactWriter(insights_root)
    writer.write(
        make_insight_artifact(
            run_id="ins_old",
            generated_at=datetime(2026, 3, 31, 11, tzinfo=UTC),
            repo_path=repo_path,
            insights=[],
        )
    )
    writer.write(
        make_insight_artifact(
            run_id="ins_new",
            generated_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
            repo_path=repo_path,
            insights=[],
        )
    )
    decision_root = tmp_path / "decision"
    decision_root.mkdir()

    loader = DecisionLoader(insights_root=insights_root, decision_root=decision_root)
    current, history = loader.load(repo=None, insight_run_id=None, history_limit=2)

    assert current.run_id == "ins_new"
    assert history == []


def test_candidate_builder_assigns_deterministic_ids() -> None:
    candidate = CandidateBuilder().build(
        CandidateSpec(
            family="test_visibility",
            subject="test_signal",
            pattern_key="unknown_persistent",
            evidence={},
            matched_rules=["rule_a"],
            proposal_outline=ProposalOutline(title_hint="t", summary_hint="s"),
        )
    )
    assert candidate.dedup_key == "candidate|test_visibility|test_signal|unknown_persistent"
    assert candidate.candidate_id == "candidate:test_visibility:test_signal:unknown_persistent"


def test_policy_suppresses_on_cooldown_and_quota() -> None:
    now = datetime(2026, 3, 31, 12, tzinfo=UTC)
    prior = ProposalCandidatesArtifact(
        run_id="dec_old",
        generated_at=now - timedelta(minutes=30),
        source_command="control-plane decide-proposals",
        repo={"name": "control-plane", "path": "/tmp/repo"},
        source_insight_run_id="ins_old",
        candidates=[
            {
                "candidate_id": "candidate:test_visibility:test_signal:unknown_persistent",
                "dedup_key": "candidate|test_visibility|test_signal|unknown_persistent",
                "family": "test_visibility",
                "subject": "test_signal",
                "status": "emit",
                "evidence": {},
                "rationale": {"matched_rules": [], "suppressed_by": []},
                "proposal_outline": {"title_hint": "t", "summary_hint": "s", "labels_hint": [], "source_family": None},
            }
        ],
        suppressed=[],
    )
    specs = [
        CandidateSpec(
            family="test_visibility",
            subject="test_signal",
            pattern_key="unknown_persistent",
            evidence={},
            matched_rules=["rule_a"],
            proposal_outline=ProposalOutline(title_hint="t", summary_hint="s"),
            priority=(0, 0, "a"),
        ),
        CandidateSpec(
            family="dependency_drift_followup",
            subject="dependency_drift",
            pattern_key="present_persistent",
            evidence={},
            matched_rules=["rule_b"],
            proposal_outline=ProposalOutline(title_hint="t2", summary_hint="s2"),
            priority=(1, 0, "b"),
        ),
        CandidateSpec(
            family="hotspot_concentration",
            subject="src/main.py",
            pattern_key="persistent",
            evidence={},
            matched_rules=["rule_c"],
            proposal_outline=ProposalOutline(title_hint="t3", summary_hint="s3"),
            priority=(2, 0, "c"),
        ),
    ]
    policy = DecisionPolicy(config=DecisionPolicyConfig(max_candidates=1, max_candidates_per_family=1, cooldown_minutes=120))
    emitted, suppressed = policy.apply(candidate_specs=specs, prior_artifacts=[prior], generated_at=now)

    assert [candidate.family for candidate in emitted] == ["dependency_drift_followup"]
    assert {item.reason for item in suppressed} == {"cooldown_active", "quota_exceeded"}


def test_service_emits_and_suppresses_candidates(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    insight = make_insight_artifact(
        run_id="ins_1",
        generated_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
        repo_path=repo_path,
        insights=[
            make_insight(
                kind="observation_coverage",
                subject="test_signal",
                dedup_key="observation_coverage|test_signal|persistent_unavailable",
                evidence={"signal": "test_signal", "consecutive_snapshots": 3},
            ),
            make_insight(
                kind="test_status_continuity",
                subject="test_signal",
                dedup_key="test_status_continuity|unknown|persistent",
                evidence={"current_status": "unknown", "consecutive_snapshots": 4},
            ),
            make_insight(
                kind="dependency_drift_continuity",
                subject="dependency_drift",
                dedup_key="dependency_drift_continuity|present|persistent",
                evidence={"consecutive_snapshots": 3},
            ),
            make_insight(
                kind="file_hotspot",
                subject="src/control_plane/watcher.py",
                dedup_key="file_hotspot|src/control_plane/watcher.py|repeated_presence",
                evidence={"appears_in_recent_snapshots": 3},
            ),
        ],
    )
    insights_root = tmp_path / "insights"
    InsightArtifactWriter(insights_root).write(insight)

    service = DecisionEngineService(loader=DecisionLoader(insights_root=insights_root, decision_root=tmp_path / "decision"), usage_store=UsageStore(tmp_path / "usage.json"))
    artifact, artifacts = service.decide(
        new_decision_context(
            repo_filter=None,
            insight_run_id=None,
            history_limit=5,
            max_candidates=2,
            cooldown_minutes=120,
            source_command="control-plane decide-proposals",
        )
    )

    assert len(artifact.candidates) == 2
    assert len(artifact.suppressed) >= 1
    assert Path(artifacts[0]).exists()


def test_zero_candidate_run_is_valid(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    insight = make_insight_artifact(
        run_id="ins_1",
        generated_at=datetime(2026, 3, 31, 12, tzinfo=UTC),
        repo_path=repo_path,
        insights=[
            make_insight(
                kind="commit_activity",
                subject="control-plane",
                dedup_key="commit_activity|recent_window",
                evidence={"current_commit_count": 2},
            )
        ],
    )
    insights_root = tmp_path / "insights"
    InsightArtifactWriter(insights_root).write(insight)
    service = DecisionEngineService(loader=DecisionLoader(insights_root=insights_root, decision_root=tmp_path / "decision"), usage_store=UsageStore(tmp_path / "usage.json"))

    artifact, _ = service.decide(
        new_decision_context(
            repo_filter=None,
            insight_run_id=None,
            history_limit=5,
            max_candidates=3,
            cooldown_minutes=120,
            source_command="control-plane decide-proposals",
        )
    )

    assert artifact.candidates == []
    assert artifact.suppressed == []
