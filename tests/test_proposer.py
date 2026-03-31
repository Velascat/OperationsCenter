from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from control_plane.config.settings import load_settings
from control_plane.decision.artifact_writer import DecisionArtifactWriter
from control_plane.decision.models import (
    CandidateRationale,
    DecisionRepoRef,
    ProposalCandidate,
    ProposalCandidatesArtifact,
    ProposalOutline,
)
from control_plane.insights.artifact_writer import InsightArtifactWriter
from control_plane.insights.models import InsightRepoRef, RepoInsightsArtifact, SourceSnapshotRef
from control_plane.proposer.candidate_integration import (
    CandidateProposerIntegrationService,
    new_proposer_integration_context,
)
from control_plane.proposer.artifact_writer import ProposerArtifactWriter
from control_plane.proposer.candidate_loader import ProposalCandidateLoader
from control_plane.proposer.candidate_mapper import ProposalCandidateMapper
from control_plane.proposer.guardrail_adapter import ProposerGuardrailAdapter
from control_plane.proposer.provenance import build_provenance


class FakePlaneClient:
    def __init__(self, issues: list[dict[str, object]] | None = None) -> None:
        self.issues = issues or []
        self.created: list[dict[str, object]] = []
        self.comments: list[tuple[str, str]] = []

    def list_issues(self) -> list[dict[str, object]]:
        return self.issues

    def create_issue(
        self,
        *,
        name: str,
        description: str,
        state: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict[str, object]:
        issue = {
            "id": f"ISSUE-{len(self.created) + 1}",
            "name": name,
            "description": description,
            "state": {"name": state or "Backlog"},
            "labels": [{"name": label} for label in (label_names or [])],
        }
        self.created.append(issue)
        self.issues.append(issue)
        return issue

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        self.comments.append((task_id, comment_markdown))


def write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "plane:",
                "  base_url: http://plane.local",
                "  api_token_env: PLANE_API_TOKEN",
                "  workspace_slug: ws",
                "  project_id: proj",
                "git: {}",
                "kodo: {}",
                "repos:",
                "  control-plane:",
                "    clone_url: git@github.com:Velascat/ControlPlane.git",
                "    default_branch: main",
                "  code_youtube_shorts:",
                "    clone_url: git@github.com:Velascat/code_youtube_shorts.git",
                "    default_branch: new-feature",
                f"report_root: {tmp_path / 'reports'}",
            ]
        )
    )
    return config_path


def make_decision_artifact(tmp_path: Path) -> tuple[ProposalCandidatesArtifact, RepoInsightsArtifact]:
    generated_at = datetime(2026, 3, 31, 13, tzinfo=UTC)
    insight = RepoInsightsArtifact(
        run_id="ins_1",
        generated_at=generated_at,
        source_command="control-plane generate-insights",
        repo=InsightRepoRef(name="control-plane", path=tmp_path / "repo"),
        source_snapshots=[SourceSnapshotRef(run_id="obs_1", observed_at=datetime(2026, 3, 31, 12, tzinfo=UTC))],
        insights=[],
    )
    decision = ProposalCandidatesArtifact(
        run_id="dec_1",
        generated_at=generated_at,
        source_command="control-plane decide-proposals",
        repo=DecisionRepoRef(name="control-plane", path=tmp_path / "repo"),
        source_insight_run_id="ins_1",
        candidates=[
            ProposalCandidate(
                candidate_id="candidate:test_visibility:test_signal:unknown_persistent",
                dedup_key="candidate|test_visibility|test_signal|unknown_persistent",
                family="test_visibility",
                subject="test_signal",
                rationale=CandidateRationale(matched_rules=["rule_a"], suppressed_by=[]),
                proposal_outline=ProposalOutline(
                    title_hint="Improve test signal visibility for control-plane",
                    summary_hint="Add one bounded path for explicit test signal visibility.",
                    labels_hint=["task-kind: goal", "source: proposer"],
                    source_family="test_visibility",
                ),
            )
        ],
        suppressed=[],
    )
    InsightArtifactWriter(tmp_path / "tools" / "report" / "control_plane" / "insights").write(insight)
    DecisionArtifactWriter(tmp_path / "tools" / "report" / "control_plane" / "decision").write(decision)
    return decision, insight


def test_candidate_loader_reads_latest_decision_and_matching_insight(tmp_path: Path) -> None:
    decision, insight = make_decision_artifact(tmp_path)
    loader = ProposalCandidateLoader(
        decision_root=tmp_path / "tools" / "report" / "control_plane" / "decision",
        insights_root=tmp_path / "tools" / "report" / "control_plane" / "insights",
    )

    loaded_decision, loaded_insight = loader.load(repo=None, decision_run_id=None)

    assert loaded_decision.run_id == decision.run_id
    assert loaded_insight.run_id == insight.run_id


def test_mapper_carries_provenance_into_task_body(tmp_path: Path) -> None:
    decision, insight = make_decision_artifact(tmp_path)
    settings = load_settings(write_config(tmp_path))
    candidate = decision.candidates[0]
    mapper = ProposalCandidateMapper()

    draft = mapper.map_to_task(
        candidate=candidate,
        settings=settings,
        provenance=build_provenance(
            candidate=candidate,
            decision_artifact=decision,
            insight_artifact=insight,
            proposer_run_id="prop_1",
        ),
    )

    assert "## Proposal Provenance" in draft.description
    assert "candidate_dedup_key: candidate|test_visibility|test_signal|unknown_persistent" in draft.description
    assert "insight_run_id: ins_1" in draft.description
    assert draft.label_names == [
        "task-kind: goal",
        "source: autonomy",
        "source: propose",
        "source-family: test_visibility",
    ]


def test_mapper_uses_repo_from_provenance_when_present(tmp_path: Path) -> None:
    decision, insight = make_decision_artifact(tmp_path)
    decision.repo.name = "code_youtube_shorts"
    settings = load_settings(write_config(tmp_path))
    candidate = decision.candidates[0]
    mapper = ProposalCandidateMapper()

    draft = mapper.map_to_task(
        candidate=candidate,
        settings=settings,
        provenance=build_provenance(
            candidate=candidate,
            decision_artifact=decision,
            insight_artifact=insight,
            proposer_run_id="prop_1",
        ),
    )

    assert "repo: code_youtube_shorts" in draft.description
    assert "base_branch: new-feature" in draft.description


def test_candidate_integration_dry_run_preserves_output_without_plane_write(tmp_path: Path) -> None:
    make_decision_artifact(tmp_path)
    settings = load_settings(write_config(tmp_path))
    client = FakePlaneClient()
    service = CandidateProposerIntegrationService(
        settings=settings,
        client=client,
        loader=ProposalCandidateLoader(
            decision_root=tmp_path / "tools" / "report" / "control_plane" / "decision",
            insights_root=tmp_path / "tools" / "report" / "control_plane" / "insights",
        ),
        guardrails=ProposerGuardrailAdapter(proposer_root=tmp_path / "tools" / "report" / "control_plane" / "proposer"),
        artifact_writer=ProposerArtifactWriter(tmp_path / "tools" / "report" / "control_plane" / "proposer"),
    )

    artifact, paths = service.run(
        new_proposer_integration_context(
            repo_filter=None,
            decision_run_id=None,
            max_create=2,
            dry_run=True,
            source_command="control-plane propose-from-candidates",
        )
    )

    assert len(artifact.created) == 1
    assert artifact.created[0].status == "dry_run"
    assert client.created == []
    assert Path(paths[0]).exists()


def test_candidate_integration_skips_existing_open_equivalent_task(tmp_path: Path) -> None:
    make_decision_artifact(tmp_path)
    settings = load_settings(write_config(tmp_path))
    client = FakePlaneClient(
        [
            {
                "id": "CP-1",
                "name": "Improve test signal visibility for control-plane",
                "description": "candidate_dedup_key: candidate|test_visibility|test_signal|unknown_persistent",
                "state": {"name": "Backlog"},
                "labels": [{"name": "task-kind: goal"}],
            }
        ]
    )
    service = CandidateProposerIntegrationService(
        settings=settings,
        client=client,
        loader=ProposalCandidateLoader(
            decision_root=tmp_path / "tools" / "report" / "control_plane" / "decision",
            insights_root=tmp_path / "tools" / "report" / "control_plane" / "insights",
        ),
        guardrails=ProposerGuardrailAdapter(proposer_root=tmp_path / "tools" / "report" / "control_plane" / "proposer"),
        artifact_writer=ProposerArtifactWriter(tmp_path / "tools" / "report" / "control_plane" / "proposer"),
    )

    artifact, _ = service.run(
        new_proposer_integration_context(
            repo_filter=None,
            decision_run_id=None,
            max_create=2,
            dry_run=False,
            source_command="control-plane propose-from-candidates",
        )
    )

    assert artifact.created == []
    assert artifact.skipped[0].reason == "existing_open_equivalent_task"


def test_candidate_integration_records_partial_plane_failure(tmp_path: Path) -> None:
    decision, _ = make_decision_artifact(tmp_path)
    decision.candidates.append(
        ProposalCandidate(
            candidate_id="candidate:dependency_drift:deps:persistent",
            dedup_key="candidate|dependency_drift|deps|persistent",
            family="dependency_drift",
            subject="deps",
            rationale=CandidateRationale(matched_rules=["rule_b"], suppressed_by=[]),
            proposal_outline=ProposalOutline(
                title_hint="Investigate persistent dependency drift",
                summary_hint="Investigate the persistent dependency drift signal with one bounded follow-up.",
                labels_hint=["task-kind: improve", "source: proposer"],
                source_family="dependency_drift",
            ),
        )
    )
    DecisionArtifactWriter(tmp_path / "tools" / "report" / "control_plane" / "decision").write(decision)
    settings = load_settings(write_config(tmp_path))

    class FailingSecondCreateClient(FakePlaneClient):
        def create_issue(self, **kwargs):  # noqa: ANN003
            if len(self.created) == 1:
                raise RuntimeError("plane exploded")
            return super().create_issue(**kwargs)

    client = FailingSecondCreateClient()
    service = CandidateProposerIntegrationService(
        settings=settings,
        client=client,
        loader=ProposalCandidateLoader(
            decision_root=tmp_path / "tools" / "report" / "control_plane" / "decision",
            insights_root=tmp_path / "tools" / "report" / "control_plane" / "insights",
        ),
        guardrails=ProposerGuardrailAdapter(proposer_root=tmp_path / "tools" / "report" / "control_plane" / "proposer"),
        artifact_writer=ProposerArtifactWriter(tmp_path / "tools" / "report" / "control_plane" / "proposer"),
    )

    artifact, _ = service.run(
        new_proposer_integration_context(
            repo_filter=None,
            decision_run_id=None,
            max_create=5,
            dry_run=False,
            source_command="control-plane propose-from-candidates",
        )
    )

    assert len(artifact.created) == 1
    assert len(artifact.failed) == 1
    assert artifact.failed[0].reason == "plane_create_failed"
