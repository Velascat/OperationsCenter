from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from operations_center.config.settings import load_settings
from operations_center.decision.artifact_writer import DecisionArtifactWriter
from operations_center.decision.models import (
    CandidateRationale,
    DecisionRepoRef,
    ProposalCandidate,
    ProposalCandidatesArtifact,
    ProposalOutline,
)
from operations_center.insights.artifact_writer import InsightArtifactWriter
from operations_center.insights.models import InsightRepoRef, RepoInsightsArtifact, SourceSnapshotRef
from operations_center.proposer.candidate_integration import (
    CandidateProposerIntegrationService,
    new_proposer_integration_context,
)
from operations_center.proposer.artifact_writer import ProposerArtifactWriter
from operations_center.proposer.candidate_loader import ProposalCandidateLoader
from operations_center.proposer.candidate_mapper import ProposalCandidateMapper
from operations_center.execution import UsageStore
from operations_center.proposer.guardrail_adapter import ProposerGuardrailAdapter
from operations_center.proposer.provenance import build_provenance


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
                "  operations-center:",
                "    clone_url: git@github.com:Velascat/OperationsCenter.git",
                "    default_branch: main",
                "  OperationsCenter:",
                "    clone_url: git@github.com:Velascat/OperationsCenter.git",
                "    default_branch: main",
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
        source_command="operations-center generate-insights",
        repo=InsightRepoRef(name="operations-center", path=tmp_path / "repo"),
        source_snapshots=[SourceSnapshotRef(run_id="obs_1", observed_at=datetime(2026, 3, 31, 12, tzinfo=UTC))],
        insights=[],
    )
    decision = ProposalCandidatesArtifact(
        run_id="dec_1",
        generated_at=generated_at,
        source_command="operations-center decide-proposals",
        repo=DecisionRepoRef(name="operations-center", path=tmp_path / "repo"),
        source_insight_run_id="ins_1",
        candidates=[
            ProposalCandidate(
                candidate_id="candidate:test_visibility:test_signal:unknown_persistent",
                dedup_key="candidate|test_visibility|test_signal|unknown_persistent",
                family="test_visibility",
                subject="test_signal",
                rationale=CandidateRationale(matched_rules=["rule_a"], suppressed_by=[]),
                proposal_outline=ProposalOutline(
                    title_hint="Improve test signal visibility for operations-center",
                    summary_hint="Add one bounded path for explicit test signal visibility.",
                    labels_hint=["task-kind: goal", "source: proposer"],
                    source_family="test_visibility",
                ),
            )
        ],
        suppressed=[],
    )
    InsightArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "insights").write(insight)
    DecisionArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "decision").write(decision)
    return decision, insight


def test_candidate_loader_reads_latest_decision_and_matching_insight(tmp_path: Path) -> None:
    decision, insight = make_decision_artifact(tmp_path)
    loader = ProposalCandidateLoader(
        decision_root=tmp_path / "tools" / "report" / "operations_center" / "decision",
        insights_root=tmp_path / "tools" / "report" / "operations_center" / "insights",
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

    assert "## Provenance" in draft.description
    assert "candidate_dedup_key: candidate|test_visibility|test_signal|unknown_persistent" in draft.description
    assert "insight_run_id: ins_1" in draft.description
    assert "task-kind: goal" in draft.label_names
    assert "source: autonomy" in draft.label_names
    assert "source: propose" in draft.label_names
    assert "source-family: test_visibility" in draft.label_names
    assert any(lbl.startswith("repo:") for lbl in draft.label_names)


def test_mapper_uses_repo_from_provenance_when_present(tmp_path: Path) -> None:
    decision, insight = make_decision_artifact(tmp_path)
    decision.repo.name = "OperationsCenter"
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

    assert "repo: OperationsCenter" in draft.description
    assert "base_branch: main" in draft.description


def test_candidate_integration_dry_run_preserves_output_without_plane_write(tmp_path: Path) -> None:
    make_decision_artifact(tmp_path)
    settings = load_settings(write_config(tmp_path))
    client = FakePlaneClient()
    service = CandidateProposerIntegrationService(
        settings=settings,
        client=client,
        loader=ProposalCandidateLoader(
            decision_root=tmp_path / "tools" / "report" / "operations_center" / "decision",
            insights_root=tmp_path / "tools" / "report" / "operations_center" / "insights",
        ),
        guardrails=ProposerGuardrailAdapter(proposer_root=tmp_path / "tools" / "report" / "operations_center" / "proposer", usage_store=UsageStore(tmp_path / "usage.json")),
        artifact_writer=ProposerArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "proposer"),
    )

    artifact, paths = service.run(
        new_proposer_integration_context(
            repo_filter=None,
            decision_run_id=None,
            max_create=2,
            dry_run=True,
            source_command="operations-center propose-from-candidates",
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
                "name": "Improve test signal visibility for operations-center",
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
            decision_root=tmp_path / "tools" / "report" / "operations_center" / "decision",
            insights_root=tmp_path / "tools" / "report" / "operations_center" / "insights",
        ),
        guardrails=ProposerGuardrailAdapter(proposer_root=tmp_path / "tools" / "report" / "operations_center" / "proposer", usage_store=UsageStore(tmp_path / "usage.json")),
        artifact_writer=ProposerArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "proposer"),
    )

    artifact, _ = service.run(
        new_proposer_integration_context(
            repo_filter=None,
            decision_run_id=None,
            max_create=2,
            dry_run=False,
            source_command="operations-center propose-from-candidates",
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
    DecisionArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "decision").write(decision)
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
            decision_root=tmp_path / "tools" / "report" / "operations_center" / "decision",
            insights_root=tmp_path / "tools" / "report" / "operations_center" / "insights",
        ),
        guardrails=ProposerGuardrailAdapter(proposer_root=tmp_path / "tools" / "report" / "operations_center" / "proposer", usage_store=UsageStore(tmp_path / "usage.json")),
        artifact_writer=ProposerArtifactWriter(tmp_path / "tools" / "report" / "operations_center" / "proposer"),
    )

    artifact, _ = service.run(
        new_proposer_integration_context(
            repo_filter=None,
            decision_run_id=None,
            max_create=5,
            dry_run=False,
            source_command="operations-center propose-from-candidates",
        )
    )

    assert len(artifact.created) == 1
    assert len(artifact.failed) == 1
    assert artifact.failed[0].reason == "plane_create_failed"


# ── recently_done guard ───────────────────────────────────────────────────────


def _guardrail(
    issues: list[dict[str, object]],
    *,
    tmp_path: Path,
    recently_done_window_days: int = 7,
) -> ProposerGuardrailAdapter:
    return ProposerGuardrailAdapter(
        proposer_root=tmp_path / "proposer",
        recently_done_window_days=recently_done_window_days,
        usage_store=UsageStore(tmp_path / "usage.json"),
        _issues_override=issues,
    )


def test_recently_done_task_blocks_reproposal(tmp_path: Path) -> None:
    """A Done task updated within the window suppresses a new proposal."""
    from operations_center.proposer.guardrail_adapter import ProposerGuardrailAdapter
    from unittest.mock import MagicMock

    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    client = MagicMock()
    client.list_issues.return_value = [
        {
            "id": "OLD-1",
            "name": "Add tests for client.py",
            "description": "",
            "state": {"name": "Done"},
            "updated_at": "2026-04-04T10:00:00+00:00",  # 1 day ago, within 7-day window
        }
    ]
    guardrail = ProposerGuardrailAdapter(
        proposer_root=tmp_path / "proposer",
        recently_done_window_days=7,
        usage_store=UsageStore(tmp_path / "usage.json"),
    )
    result = guardrail.evaluate(
        client=client,
        dedup_key="candidate|test_coverage|client_py|persistent",
        title="Add tests for client.py",
        now=now,
    )
    assert not result.allowed
    assert result.reason == "recently_completed_equivalent_task"
    assert result.evidence["plane_issue_id"] == "OLD-1"
    assert result.evidence["recently_done_window_days"] == 7


def test_old_done_task_outside_window_does_not_block(tmp_path: Path) -> None:
    """A Done task updated outside the window does not suppress."""
    from operations_center.proposer.guardrail_adapter import ProposerGuardrailAdapter
    from unittest.mock import MagicMock

    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    client = MagicMock()
    client.list_issues.return_value = [
        {
            "id": "OLD-2",
            "name": "Add tests for client.py",
            "description": "",
            "state": {"name": "Done"},
            "updated_at": "2026-03-20T10:00:00+00:00",  # 16 days ago, outside 7-day window
        }
    ]
    guardrail = ProposerGuardrailAdapter(
        proposer_root=tmp_path / "proposer",
        recently_done_window_days=7,
        usage_store=UsageStore(tmp_path / "usage.json"),
    )
    result = guardrail.evaluate(
        client=client,
        dedup_key="candidate|test_coverage|client_py|persistent",
        title="Add tests for client.py",
        now=now,
    )
    assert result.allowed


def test_recently_done_window_zero_disables_guard(tmp_path: Path) -> None:
    """recently_done_window_days=0 disables the guard entirely."""
    from operations_center.proposer.guardrail_adapter import ProposerGuardrailAdapter
    from unittest.mock import MagicMock

    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    client = MagicMock()
    client.list_issues.return_value = [
        {
            "id": "OLD-3",
            "name": "Add tests for client.py",
            "description": "",
            "state": {"name": "Done"},
            "updated_at": "2026-04-05T01:00:00+00:00",  # today, but window=0
        }
    ]
    guardrail = ProposerGuardrailAdapter(
        proposer_root=tmp_path / "proposer",
        recently_done_window_days=0,
        usage_store=UsageStore(tmp_path / "usage.json"),
    )
    result = guardrail.evaluate(
        client=client,
        dedup_key="candidate|test_coverage|client_py|persistent",
        title="Add tests for client.py",
        now=now,
    )
    assert result.allowed


def test_recently_done_matches_by_dedup_key_in_description(tmp_path: Path) -> None:
    """Done task matched by dedup_key in description (not title) also blocks."""
    from operations_center.proposer.guardrail_adapter import ProposerGuardrailAdapter
    from unittest.mock import MagicMock

    now = datetime(2026, 4, 5, 12, 0, 0, tzinfo=UTC)
    client = MagicMock()
    client.list_issues.return_value = [
        {
            "id": "OLD-4",
            "name": "Some other title",
            "description": "candidate_dedup_key: candidate|lint_fix|src|persistent",
            "state": {"name": "Done"},
            "updated_at": "2026-04-03T10:00:00+00:00",
        }
    ]
    guardrail = ProposerGuardrailAdapter(
        proposer_root=tmp_path / "proposer",
        recently_done_window_days=7,
        usage_store=UsageStore(tmp_path / "usage.json"),
    )
    result = guardrail.evaluate(
        client=client,
        dedup_key="candidate|lint_fix|src|persistent",
        title="Fix lint issues in src/",
        now=now,
    )
    assert not result.allowed
    assert result.reason == "recently_completed_equivalent_task"
