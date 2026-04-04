from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from control_plane.adapters.plane import PlaneClient
from control_plane.execution import UsageStore
from control_plane.proposer.result_models import ProposalResultsArtifact


@dataclass(frozen=True)
class GuardrailResult:
    allowed: bool
    reason: str | None = None
    evidence: dict[str, object] | None = None


class ProposerGuardrailAdapter:
    def __init__(
        self,
        *,
        proposer_root: Path | None = None,
        cooldown_minutes: int = 120,
        usage_store: UsageStore | None = None,
    ) -> None:
        self.proposer_root = proposer_root or Path("tools/report/control_plane/proposer")
        self.cooldown_minutes = cooldown_minutes
        self._usage_store = usage_store

    def evaluate(self, *, client: PlaneClient, dedup_key: str, title: str, now: datetime) -> GuardrailResult:
        usage_store = self._usage_store or UsageStore()
        remaining = usage_store.remaining_exec_capacity(now=now)
        min_remaining = usage_store.settings.min_remaining_exec_for_proposals
        if remaining < min_remaining:
            usage_store.record_proposal_budget_suppression(
                reason="proposal_budget_too_low",
                now=now,
                evidence={"remaining_exec_capacity": remaining, "min_required": min_remaining},
            )
            return GuardrailResult(
                allowed=False,
                reason="proposal_budget_too_low",
                evidence={"remaining_exec_capacity": remaining, "min_required": min_remaining},
            )
        open_match = self._find_open_task_match(client, dedup_key=dedup_key, title=title)
        if open_match is not None:
            return GuardrailResult(
                allowed=False,
                reason="existing_open_equivalent_task",
                evidence={"plane_issue_id": open_match[0], "plane_title": open_match[1]},
            )
        last_created = self._last_created_at(dedup_key)
        if last_created is not None:
            cooldown_cutoff = now - timedelta(minutes=self.cooldown_minutes)
            if last_created >= cooldown_cutoff:
                return GuardrailResult(
                    allowed=False,
                    reason="cooldown_active",
                    evidence={
                        "last_created_at": last_created.isoformat(),
                        "cooldown_minutes": self.cooldown_minutes,
                    },
                )
        return GuardrailResult(allowed=True)

    def _find_open_task_match(self, client: PlaneClient, *, dedup_key: str, title: str) -> tuple[str, str] | None:
        title_normalized = title.strip().lower()
        key_normalized = dedup_key.strip().lower()
        for issue in client.list_issues():
            state_name = ""
            state = issue.get("state")
            if isinstance(state, dict):
                state_name = str(state.get("name", "")).strip().lower()
            if state_name in {"done", "cancelled"}:
                continue
            name = str(issue.get("name", "")).strip().lower()
            description = str(issue.get("description") or issue.get("description_stripped") or "")
            if name == title_normalized:
                return str(issue.get("id")), str(issue.get("name", ""))
            for line in description.splitlines():
                if line.strip().lower() == f"candidate_dedup_key: {key_normalized}":
                    return str(issue.get("id")), str(issue.get("name", ""))
                if line.strip().lower() == f"- proposal_dedup_key: {key_normalized}":
                    return str(issue.get("id")), str(issue.get("name", ""))
        return None

    def _last_created_at(self, dedup_key: str) -> datetime | None:
        paths = sorted(
            self.proposer_root.glob("*/proposal_results.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in paths:
            artifact = ProposalResultsArtifact.model_validate_json(path.read_text())
            for item in artifact.created:
                if item.dedup_key == dedup_key and item.status in {"created", "dry_run"}:
                    return artifact.generated_at
        return None
