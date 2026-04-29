# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.execution import UsageStore
from operations_center.proposer.rejection_store import ProposalRejectionStore
from operations_center.proposer.result_models import ProposalResultsArtifact

_RECENTLY_DONE_WINDOW_DAYS = 7


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
        recently_done_window_days: int = _RECENTLY_DONE_WINDOW_DAYS,
        usage_store: UsageStore | None = None,
        rejection_store: ProposalRejectionStore | None = None,
    ) -> None:
        self.proposer_root = proposer_root or Path("tools/report/operations_center/proposer")
        self.cooldown_minutes = cooldown_minutes
        self.recently_done_window_days = recently_done_window_days
        self._usage_store = usage_store
        self._rejection_store = rejection_store or ProposalRejectionStore()

    def evaluate(self, *, client: PlaneClient, dedup_key: str, title: str, now: datetime) -> GuardrailResult:
        # Long-lived rejection check comes first — human "no" signals are permanent.
        if self._rejection_store.is_rejected(dedup_key):
            return GuardrailResult(
                allowed=False,
                reason="permanently_rejected_by_human",
                evidence={"dedup_key": dedup_key},
            )
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
        done_match = self._find_recently_done_match(client, dedup_key=dedup_key, title=title, now=now)
        if done_match is not None:
            return GuardrailResult(
                allowed=False,
                reason="recently_completed_equivalent_task",
                evidence={
                    "plane_issue_id": done_match[0],
                    "plane_title": done_match[1],
                    "recently_done_window_days": self.recently_done_window_days,
                },
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

    def _find_recently_done_match(
        self, client: PlaneClient, *, dedup_key: str, title: str, now: datetime
    ) -> tuple[str, str] | None:
        """Return (id, title) of any Done/Cancelled task matching by title or dedup_key
        that was updated within recently_done_window_days."""
        if self.recently_done_window_days <= 0:
            return None
        cutoff = now - timedelta(days=self.recently_done_window_days)
        title_normalized = title.strip().lower()
        key_normalized = dedup_key.strip().lower()
        for issue in client.list_issues():
            state_name = ""
            state = issue.get("state")
            if isinstance(state, dict):
                state_name = str(state.get("name", "")).strip().lower()
            if state_name not in {"done", "cancelled"}:
                continue
            updated_raw = issue.get("updated_at") or issue.get("completed_at") or ""
            if updated_raw:
                try:
                    updated_at = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00"))
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    if updated_at < cutoff:
                        continue
                except ValueError:
                    pass
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
