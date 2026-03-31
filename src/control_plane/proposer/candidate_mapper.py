from __future__ import annotations

from dataclasses import dataclass

from control_plane.config.settings import Settings
from control_plane.decision.models import ProposalCandidate
from control_plane.proposer.provenance import ProposalProvenance


@dataclass(frozen=True)
class PlaneTaskDraft:
    name: str
    description: str
    state: str
    label_names: list[str]
    task_kind: str


class ProposalCandidateMapper:
    def map_to_task(
        self,
        *,
        candidate: ProposalCandidate,
        settings: Settings,
        provenance: ProposalProvenance,
    ) -> PlaneTaskDraft:
        repo_key = self._repo_key_for_candidate(settings=settings, provenance=provenance)
        repo_cfg = settings.repos[repo_key]
        task_kind = self._task_kind_for_candidate(candidate)
        state = "Ready for AI" if task_kind == "goal" else "Backlog"
        allowed_paths = self._allowed_paths(repo_key)
        lines = [
            "## Execution",
            f"repo: {repo_key}",
            f"base_branch: {repo_cfg.default_branch}",
            "mode: goal",
        ]
        if allowed_paths:
            lines.append("allowed_paths:")
            for path in allowed_paths:
                lines.append(f"  - {path}")
        lines.extend(["", "## Goal", candidate.proposal_outline.summary_hint])
        lines.extend(["", "## Constraints"])
        lines.extend(self._constraints_for_candidate(candidate))
        lines.extend(["", "## Proposal Provenance"])
        lines.extend(
            [
                f"source: {provenance.source}",
                f"source_family: {provenance.source_family}",
                f"candidate_id: {provenance.candidate_id}",
                f"candidate_dedup_key: {provenance.candidate_dedup_key}",
                "observer_run_ids:",
            ]
        )
        for run_id in provenance.observer_run_ids:
            lines.append(f"  - {run_id}")
        lines.extend(
            [
                f"insight_run_id: {provenance.insight_run_id}",
                f"decision_run_id: {provenance.decision_run_id}",
                f"proposer_run_id: {provenance.proposer_run_id}",
            ]
        )
        return PlaneTaskDraft(
            name=candidate.proposal_outline.title_hint,
            description="\n".join(lines).strip(),
            state=state,
            label_names=[
                f"task-kind: {task_kind}",
                "source: autonomy",
                "source: propose",
                f"source-family: {candidate.family}",
            ],
            task_kind=task_kind,
        )

    @staticmethod
    def _repo_key_for_candidate(*, settings: Settings, provenance: ProposalProvenance) -> str:
        if provenance.repo_name in settings.repos:
            return provenance.repo_name
        for repo_key in settings.repos:
            if repo_key.strip().lower() == provenance.repo_name.strip().lower():
                return repo_key
        return next(iter(settings.repos.keys()))

    @staticmethod
    def _task_kind_for_candidate(candidate: ProposalCandidate) -> str:
        for label in candidate.proposal_outline.labels_hint:
            normalized = label.strip().lower()
            if normalized.startswith("task-kind:"):
                return normalized.split(":", 1)[1].strip()
        if candidate.family in {"observation_coverage", "test_visibility"}:
            return "goal"
        return "improve"

    @staticmethod
    def _allowed_paths(repo_key: str) -> list[str]:
        if repo_key.strip().lower() in {"controlplane", "control-plane"}:
            return ["src/", "tests/", "docs/"]
        return []

    @staticmethod
    def _constraints_for_candidate(candidate: ProposalCandidate) -> list[str]:
        family_scopes = {
            "test_visibility": [
                "- Keep the change scoped to explicit test signal visibility or stabilization.",
                "- Do not expand into unrelated repo-wide testing refactors.",
            ],
            "dependency_drift_followup": [
                "- Keep the change scoped to the persistent dependency drift signal.",
                "- Do not introduce unrelated dependency churn.",
            ],
            "hotspot_concentration": [
                "- Keep the task focused on the identified hotspot area.",
                "- Do not expand into broad architectural rewrites.",
            ],
            "todo_accumulation": [
                "- Keep the change scoped to the identified TODO/FIXME concentration.",
                "- Do not convert this into a general cleanup sweep.",
            ],
            "observation_coverage": [
                "- Keep the change scoped to restoring missing autonomy visibility.",
                "- Preserve existing observer and insight contracts unless directly required.",
            ],
        }
        return family_scopes.get(
            candidate.family,
            [
                "- Keep the change scoped to the identified issue.",
                "- Do not expand into unrelated refactors.",
            ],
        )
