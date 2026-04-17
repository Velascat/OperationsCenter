from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from control_plane.adapters.plane import PlaneClient
from control_plane.config.settings import Settings
from control_plane.decision.models import ProposalCandidate
from control_plane.proposer.artifact_writer import ProposerArtifactWriter
from control_plane.proposer.candidate_loader import ProposalCandidateLoader
from control_plane.proposer.candidate_mapper import ProposalCandidateMapper
from control_plane.proposer.guardrail_adapter import ProposerGuardrailAdapter
from control_plane.proposer.provenance import build_provenance
from control_plane.proposer.result_models import (
    CreatedProposalResult,
    FailedProposalResult,
    ProposalResultsArtifact,
    ProposerRepoRef,
    SkippedProposalResult,
)
from control_plane.spec_director.suppressor import is_suppressed as _spec_suppressed


class CandidateLoaderProtocol(Protocol):
    def load(self, *, repo: str | None, decision_run_id: str | None): ...


@dataclass(frozen=True)
class ProposerIntegrationContext:
    repo_filter: str | None
    decision_run_id: str | None
    run_id: str
    generated_at: datetime
    source_command: str
    max_create: int
    dry_run: bool


class CandidateProposerIntegrationService:
    def __init__(
        self,
        *,
        settings: Settings,
        client: PlaneClient,
        loader: CandidateLoaderProtocol | None = None,
        mapper: ProposalCandidateMapper | None = None,
        guardrails: ProposerGuardrailAdapter | None = None,
        artifact_writer: ProposerArtifactWriter | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.loader = loader or ProposalCandidateLoader()
        self.mapper = mapper or ProposalCandidateMapper()
        self.guardrails = guardrails or ProposerGuardrailAdapter()
        self.artifact_writer = artifact_writer or ProposerArtifactWriter()

    def run(self, context: ProposerIntegrationContext) -> tuple[ProposalResultsArtifact, list[str]]:
        decision_artifact, insight_artifact = self.loader.load(
            repo=context.repo_filter,
            decision_run_id=context.decision_run_id,
        )
        candidates = [candidate for candidate in decision_artifact.candidates if candidate.status == "emit"]
        created: list[CreatedProposalResult] = []
        skipped: list[SkippedProposalResult] = []
        failed: list[FailedProposalResult] = []

        _active_campaign_list: list = []
        try:
            from control_plane.spec_director.state import CampaignStateManager
            _active_campaign_list = CampaignStateManager().load().active_campaigns()
        except Exception:
            _active_campaign_list = []

        for candidate in candidates:
            if len(created) >= context.max_create:
                skipped.append(
                    SkippedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        reason="max_create_reached",
                        evidence={"max_create": context.max_create},
                    )
                )
                continue

            _paths = [str(f) for f in getattr(candidate, "changed_files", [])] + \
                     [str(f) for f in getattr(candidate, "target_paths", [])]
            _title = candidate.proposal_outline.title_hint or candidate.subject
            if _spec_suppressed(_title, _paths, _active_campaign_list):
                skipped.append(
                    SkippedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        reason="active_spec_campaign",
                        evidence={},
                    )
                )
                continue

            try:
                draft = self.mapper.map_to_task(
                    candidate=candidate,
                    settings=self.settings,
                    provenance=build_provenance(
                        candidate=candidate,
                        decision_artifact=decision_artifact,
                        insight_artifact=insight_artifact,
                        proposer_run_id=context.run_id,
                    ),
                )
            except Exception as exc:
                failed.append(
                    FailedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        reason="candidate_mapping_failed",
                        error=str(exc),
                    )
                )
                continue

            guardrail = self.guardrails.evaluate(
                client=self.client,
                dedup_key=candidate.dedup_key,
                title=draft.name,
                now=context.generated_at,
            )
            if not guardrail.allowed:
                skipped.append(
                    SkippedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        reason=guardrail.reason or "guardrail_blocked",
                        evidence=guardrail.evidence or {},
                    )
                )
                continue

            if context.dry_run:
                created.append(
                    CreatedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        plane_issue_id=None,
                        plane_title=draft.name,
                        status="dry_run",
                    )
                )
                continue

            try:
                issue = self.client.create_issue(
                    name=draft.name,
                    description=draft.description,
                    state=draft.state,
                    label_names=draft.label_names,
                )
                self.client.comment_issue(
                    str(issue.get("id")),
                    self._created_comment(candidate=candidate, draft=draft, decision_run_id=decision_artifact.run_id),
                )
                created.append(
                    CreatedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        plane_issue_id=str(issue.get("id")),
                        plane_title=draft.name,
                        status="created",
                    )
                )
            except Exception as exc:
                failed.append(
                    FailedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        reason="plane_create_failed",
                        error=str(exc),
                    )
                )

        artifact = ProposalResultsArtifact(
            run_id=context.run_id,
            generated_at=context.generated_at,
            source_command=context.source_command,
            repo=ProposerRepoRef(name=decision_artifact.repo.name, path=decision_artifact.repo.path),
            source_decision_run_id=decision_artifact.run_id,
            dry_run=context.dry_run,
            created=created,
            skipped=skipped,
            failed=failed,
        )
        return artifact, self.artifact_writer.write(artifact)

    @staticmethod
    def _created_comment(*, candidate: ProposalCandidate, draft, decision_run_id: str) -> str:
        lines = [
            "[Propose] Candidate task created",
            f"- task_kind: {draft.task_kind}",
            "- result_status: created",
            f"- source_family: {candidate.family}",
            f"- candidate_id: {candidate.candidate_id}",
            f"- dedup_key: {candidate.dedup_key}",
            f"- decision_run_id: {decision_run_id}",
            f"- proposer_run_id: {draft.description.split('proposer_run_id: ', 1)[1].splitlines()[0] if 'proposer_run_id:' in draft.description else 'unknown'}",
            f"- handoff_reason: proposer_candidate_{candidate.family}",
        ]
        return "\n".join(lines)


def new_proposer_integration_context(
    *,
    repo_filter: str | None,
    decision_run_id: str | None,
    max_create: int,
    dry_run: bool,
    source_command: str,
) -> ProposerIntegrationContext:
    generated_at = datetime.now(UTC)
    run_id = f"prop_{generated_at.strftime('%Y%m%dT%H%M%SZ')}_{generated_at.microsecond:06x}"[-32:]
    return ProposerIntegrationContext(
        repo_filter=repo_filter,
        decision_run_id=decision_run_id,
        run_id=run_id,
        generated_at=generated_at,
        source_command=source_command,
        max_create=max_create,
        dry_run=dry_run,
    )
