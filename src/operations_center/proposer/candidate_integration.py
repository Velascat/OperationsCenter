# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from operations_center.adapters.plane import PlaneClient
from operations_center.config.settings import Settings
from operations_center.decision.models import ProposalCandidate
from operations_center.proposer.artifact_writer import ProposerArtifactWriter
from operations_center.proposer.candidate_loader import ProposalCandidateLoader
from operations_center.proposer.candidate_mapper import ProposalCandidateMapper
from operations_center.proposer.guardrail_adapter import ProposerGuardrailAdapter
from operations_center.proposer.provenance import build_provenance
from operations_center.proposer.result_models import (
    CreatedProposalResult,
    FailedProposalResult,
    ProposalResultsArtifact,
    ProposerRepoRef,
    SkippedProposalResult,
)
from operations_center.spec_director.suppressor import is_suppressed as _spec_suppressed


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
            from operations_center.spec_director.state import CampaignStateManager
            _active_campaign_list = CampaignStateManager().load().active_campaigns()
        except Exception:
            _active_campaign_list = []

        # Back-pressure gate: if the Ready-for-AI queue is already saturated
        # there's no point generating more proposals — they'd just sink to the
        # bottom of the queue and add review noise. Honors
        # Settings.propose_skip_when_ready_count (default 8). 0 disables.
        _ready_cap = int(getattr(self.settings, "propose_skip_when_ready_count", 0) or 0)
        _queue_saturated = False
        _ready_count = 0
        if _ready_cap > 0:
            try:
                _all_issues = self.client.list_issues()
                _ready_count = sum(
                    1 for _i in _all_issues
                    if (str((_i.get("state") or {}).get("name", "")
                           if isinstance(_i.get("state"), dict) else "").strip().lower())
                       == "ready for ai"
                )
                if _ready_count >= _ready_cap:
                    _queue_saturated = True
            except Exception:
                pass  # if we can't measure the queue, fall through and proceed

        # Honors RepoSettings.propose_enabled — repos with this False are
        # excluded from proposal generation entirely. Built once so the
        # per-candidate loop below is a cheap lookup.
        _propose_disabled_repos = {
            rk for rk, rcfg in (self.settings.repos or {}).items()
            if not getattr(rcfg, "propose_enabled", True)
        }

        for candidate in candidates:
            if _queue_saturated:
                skipped.append(
                    SkippedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        reason="ready_queue_saturated",
                        evidence={"ready_count": _ready_count, "cap": _ready_cap},
                    )
                )
                continue

            # propose_enabled per-repo gate. Resolve the candidate's repo
            # from provenance — same path the mapper uses.
            _cand_repo = (
                getattr(getattr(candidate, "provenance", None), "repo_key", None)
                or getattr(candidate, "repo_key", None)
                or ""
            )
            if _cand_repo in _propose_disabled_repos:
                skipped.append(
                    SkippedProposalResult(
                        candidate_id=candidate.candidate_id,
                        dedup_key=candidate.dedup_key,
                        family=candidate.family,
                        reason="propose_disabled_for_repo",
                        evidence={"repo_key": _cand_repo},
                    )
                )
                continue


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
            if _spec_suppressed(_title, _paths, _active_campaign_list, specs_dir=Path("docs/specs")):
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
