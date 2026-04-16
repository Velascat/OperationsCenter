# src/control_plane/spec_director/recovery.py
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from control_plane.spec_director._claude_cli import call_claude
from control_plane.spec_director.models import CampaignRecord
from control_plane.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)


class RecoveryService:
    def __init__(
        self,
        client: Any,
        state_manager: CampaignStateManager,
        stall_hours: int = 24,
        abandon_hours: int = 72,
        spec_revision_budget: int = 3,
    ) -> None:
        self._client = client
        self._state = state_manager
        self._stall_hours = stall_hours
        self._abandon_hours = abandon_hours
        self._budget = spec_revision_budget

    def is_stalled(self, campaign: CampaignRecord) -> bool:
        ts_str = campaign.last_progress_at or campaign.created_at
        try:
            last = datetime.fromisoformat(ts_str)
        except Exception:
            return True
        elapsed = (datetime.now(UTC) - last).total_seconds() / 3600
        return elapsed > self._stall_hours

    def should_abandon(self, campaign: CampaignRecord) -> bool:
        try:
            created = datetime.fromisoformat(campaign.created_at)
        except Exception:
            return True
        elapsed = (datetime.now(UTC) - created).total_seconds() / 3600
        return elapsed > self._abandon_hours

    def revision_budget_ok(self, campaign: CampaignRecord) -> bool:
        return campaign.spec_revision_count < self._budget

    def revise_spec(
        self,
        campaign: CampaignRecord,
        violations: list[str],
        spec_file_path: Path,
        model: str = "claude-sonnet-4-6",
    ) -> bool:
        """Revise the failing spec section via the claude CLI. Returns True on success."""
        if not self.revision_budget_ok(campaign):
            logger.warning(
                '{"event": "spec_revision_budget_exhausted", "campaign_id": "%s"}',
                campaign.campaign_id,
            )
            return False
        if not spec_file_path.exists():
            return False
        spec_text = spec_file_path.read_text()
        prompt = (
            "The following spec compliance violations were found:\n"
            + "\n".join(f"- {v}" for v in violations)
            + f"\n\nOriginal spec:\n{spec_text}\n\n"
            + "Revise the spec to resolve these violations. "
            + "Return the full revised spec document with updated YAML front matter."
        )
        try:
            revised = call_claude(prompt, model=model)
            spec_file_path.write_text(revised)
            self._state.increment_revision_count(campaign.campaign_id)
            logger.info(
                '{"event": "spec_revised", "campaign_id": "%s"}',
                campaign.campaign_id,
            )
            return True
        except Exception as exc:
            logger.error(
                '{"event": "spec_revision_failed", "campaign_id": "%s", "error": "%s"}',
                campaign.campaign_id, str(exc),
            )
            return False

    def self_cancel(
        self,
        campaign: CampaignRecord,
        reason: str,
        specs_dir: Path,
    ) -> None:
        """Perform orderly campaign self-cancellation."""
        logger.info(
            '{"event": "campaign_self_cancel", "campaign_id": "%s", "reason": "%s"}',
            campaign.campaign_id, reason,
        )
        # Cancel all open Plane tasks for this campaign
        try:
            issues = self._client.list_issues()
            for issue in issues:
                labels = [str(lbl.get("name", "")).lower() for lbl in (issue.get("labels") or [])]
                if f"campaign-id: {campaign.campaign_id}" in labels:
                    state_name = str((issue.get("state") or {}).get("name", "")).lower()
                    if state_name not in {"done", "cancelled"}:
                        self._client.update_issue(
                            str(issue["id"]),
                            {"state": "Cancelled"},
                        )
        except Exception as exc:
            logger.warning(
                '{"event": "campaign_cancel_issues_error", "error": "%s"}', str(exc)
            )

        # Update spec front matter
        spec_path = specs_dir / f"{campaign.slug}.md"
        if spec_path.exists():
            text = spec_path.read_text()
            spec_path.write_text(text.replace("status: active", "status: cancelled", 1))

        # Mark campaign cancelled in state
        self._state.mark_cancelled(campaign.campaign_id)
        logger.info(
            '{"event": "campaign_cancelled", "campaign_id": "%s"}',
            campaign.campaign_id,
        )
