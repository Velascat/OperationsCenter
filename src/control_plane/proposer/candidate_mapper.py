from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from control_plane.autonomy_tiers.config import AutonomyTiersConfig, get_family_tier, load_tiers_config
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
        tiers_config: AutonomyTiersConfig | None = None,
    ) -> PlaneTaskDraft:
        if tiers_config is None:
            tiers_config = load_tiers_config()
        repo_key = self._repo_key_for_candidate(settings=settings, provenance=provenance)
        repo_cfg = settings.repos[repo_key]
        task_kind = self._task_kind_for_candidate(candidate)
        tier = get_family_tier(candidate.family, tiers_config)
        if tier <= 0:
            # Tier 0 families should not be auto-created; caller is responsible for skipping them.
            # Defensive fallback: put in Backlog so it's visible but not auto-executed.
            state = "Backlog"
        elif tier >= 2:
            state = "Ready for AI"
        else:
            # Tier 1: respect risk_class as secondary heuristic
            state = "Ready for AI" if candidate.risk_class == "style" else "Backlog"
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
        expires_at = (datetime.now(UTC) + timedelta(days=candidate.expires_after_runs * 2)).strftime("%Y-%m-%d")
        lines.extend(["", "## Provenance"])
        requires_human_approval = state == "Backlog"
        evidence_schema_version = (
            candidate.evidence_bundle.schema_version
            if candidate.evidence_bundle is not None
            else 1
        )
        lines.extend(
            [
                f"source: {provenance.source}",
                f"source_family: {provenance.source_family}",
                f"candidate_id: {provenance.candidate_id}",
                f"candidate_dedup_key: {provenance.candidate_dedup_key}",
                f"confidence: {candidate.confidence}",
                f"risk_class: {candidate.risk_class}",
                f"autonomy_tier: {tier}",
                f"validation_profile: {candidate.validation_profile}",
                f"requires_human_approval: {'true' if requires_human_approval else 'false'}",
                f"evidence_schema_version: {evidence_schema_version}",
                # TODO (Phase 4 — proposal_generation_run_id) [deferred, reviewed 2026-04-07]
                # Add proposal_generation_run_id once autonomy_cycle assigns a stable
                # cycle-level run_id distinct from the decision run_id. This field will
                # link the task back to the specific cycle_<ts>.json report that
                # originated it. For now, decision_run_id serves as the closest proxy.
                # Unlock condition: autonomy_cycle refactored to thread a cycle-level run_id.
                # See docs/design/roadmap.md §Phase 4.
                f"expires_at: {expires_at}",
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
        if candidate.evidence_lines:
            lines.extend(["", "## Evidence"])
            for ev_line in candidate.evidence_lines:
                lines.append(f"- {ev_line}")
        self_repo_key = getattr(settings, "self_repo_key", None)
        is_self = (
            self_repo_key is not None
            and repo_key.strip().lower() == self_repo_key.strip().lower()
        )
        label_names = [
            f"task-kind: {task_kind}",
            f"repo: {repo_key}",
            "source: autonomy",
            "source: propose",
            f"source-family: {candidate.family}",
        ]
        if is_self:
            label_names.append("self-modify: approved")
        return PlaneTaskDraft(
            name=candidate.proposal_outline.title_hint,
            description="\n".join(lines).strip(),
            state=state,
            label_names=label_names,
            task_kind=task_kind,
        )

    @staticmethod
    def _repo_key_for_candidate(*, settings: Settings, provenance: ProposalProvenance) -> str:
        if provenance.repo_name in settings.repos:
            return provenance.repo_name
        for repo_key in settings.repos:
            if repo_key.strip().lower() == provenance.repo_name.strip().lower():
                return repo_key
        known = sorted(settings.repos.keys())
        raise ValueError(
            f"Proposal candidate references repo '{provenance.repo_name}' which is not in the configured repos: {known}. "
            f"Update the candidate provenance or add the repo to config."
        )

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
            "lint_fix": [
                "- Use `ruff check --fix` to auto-fix where possible, then manually resolve remaining violations.",
                "- Keep the change scoped to lint fixes only — do not refactor or change logic.",
                "- Do not suppress violations with `# noqa` unless there is a documented reason.",
                "- Do not modify more than 20 files; if the scope is broader, address the highest-severity files first.",
            ],
            "type_fix": [
                "- Resolve type errors with targeted annotations; avoid broad `# type: ignore` suppressions.",
                "- Keep the change scoped to type fixes only — do not refactor or change logic.",
                "- If a suppression is unavoidable, add a comment explaining why.",
                "- Do not modify more than 20 files; if the scope is broader, address the highest-severity files first.",
            ],
            "ci_pattern": [
                "- Investigate root cause before proposing a fix; do not suppress or skip failing checks.",
                "- Keep the change scoped to the identified failing or flaky checks.",
                "- Document findings even if no code change is needed.",
            ],
            "validation_pattern_followup": [
                "- Investigate the validation artifacts for the identified tasks before proposing changes.",
                "- Fix the root cause (broken test, misconfigured validator, or task scope issue).",
                "- Do not simply suppress or skip the failing validation step.",
            ],
        }
        return family_scopes.get(
            candidate.family,
            [
                "- Keep the change scoped to the identified issue.",
                "- Do not expand into unrelated refactors.",
            ],
        )
