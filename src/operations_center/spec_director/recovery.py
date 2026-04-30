# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
# src/operations_center/spec_director/recovery.py
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from operations_center.spec_director.models import CampaignRecord
from operations_center.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)


class RecoveryService:
    def __init__(
        self,
        client: Any,
        state_manager: CampaignStateManager,
        abandon_hours: int = 72,
    ) -> None:
        self._client = client
        self._state = state_manager
        self._abandon_hours = abandon_hours

    def should_abandon(self, campaign: CampaignRecord) -> bool:
        """True if campaign has been active beyond abandon_hours."""
        try:
            created = datetime.fromisoformat(campaign.created_at)
        except Exception:
            return True
        elapsed = (datetime.now(UTC) - created).total_seconds() / 3600
        return elapsed > self._abandon_hours

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
                        self._client.transition_issue(str(issue["id"]), "Cancelled")
        except Exception as exc:
            logger.warning(
                '{"event": "campaign_cancel_issues_error", "error": "%s"}', str(exc)
            )

        # Update spec front matter
        spec_path = specs_dir / f"{campaign.slug}.md"
        if spec_path.exists():
            text = spec_path.read_text(encoding="utf-8")
            spec_path.write_text(text.replace("status: active", "status: cancelled", 1), encoding="utf-8")

        # Mark campaign cancelled in state
        self._state.mark_cancelled(campaign.campaign_id)
        logger.info(
            '{"event": "campaign_cancelled", "campaign_id": "%s"}',
            campaign.campaign_id,
        )
