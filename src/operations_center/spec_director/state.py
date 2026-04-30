# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
# src/operations_center/spec_director/state.py
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime  # UTC kept for corrupt-file timestamp
from pathlib import Path
from typing import Literal

from operations_center.spec_director.models import ActiveCampaigns, CampaignRecord

_DEFAULT_STATE_PATH = Path("state/campaigns/active.json")
logger = logging.getLogger(__name__)


class CampaignStateManager:
    def __init__(self, state_path: Path | None = None) -> None:
        self.state_path = state_path or _DEFAULT_STATE_PATH

    def load(self) -> ActiveCampaigns:
        if not self.state_path.exists():
            return ActiveCampaigns()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return ActiveCampaigns.model_validate(data)
        except Exception as exc:
            corrupt_path = self.state_path.with_suffix(
                f".json.corrupt.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
            )
            try:
                self.state_path.rename(corrupt_path)
            except OSError:
                pass
            logger.error(
                '{"event": "spec_campaign_state_corrupt", "error": "%s", "renamed_to": "%s"}',
                str(exc), str(corrupt_path),
            )
            return ActiveCampaigns()

    def save(self, state: ActiveCampaigns) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.rename(self.state_path)

    def add_campaign(self, record: CampaignRecord) -> None:
        state = self.load()
        state.campaigns.append(record)
        self.save(state)

    def mark_complete(self, campaign_id: str) -> None:
        self._update_status(campaign_id, "complete")

    def mark_cancelled(self, campaign_id: str) -> None:
        self._update_status(campaign_id, "cancelled")

    def _update_status(
        self,
        campaign_id: str,
        status: Literal["active", "complete", "cancelled", "partial"],
    ) -> None:
        state = self.load()
        found = False
        for c in state.campaigns:
            if c.campaign_id == campaign_id:
                c.status = status
                found = True
        if found:
            self.save(state)

    def rebuild_from_specs(self, specs_dir: Path) -> ActiveCampaigns:
        """Rebuild active campaigns list by scanning spec front matter."""
        from operations_center.spec_director.models import SpecFrontMatter
        campaigns = []
        for spec_file in sorted(specs_dir.glob("*.md")):
            try:
                fm = SpecFrontMatter.from_spec_text(spec_file.read_text(encoding="utf-8"))
                if fm.status == "active":
                    campaigns.append(CampaignRecord(
                        campaign_id=fm.campaign_id,
                        slug=fm.slug,
                        spec_file=str(spec_file),
                        status="active",
                        created_at=fm.created_at,
                    ))
            except Exception as exc:
                logger.warning(
                    '{"event": "spec_rebuild_skip", "file": "%s", "error": "%s"}',
                    str(spec_file), str(exc),
                )
                continue
        rebuilt = ActiveCampaigns(campaigns=campaigns)
        self.save(rebuilt)
        return rebuilt
