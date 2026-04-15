# src/control_plane/spec_director/suppressor.py
from __future__ import annotations

import logging

from control_plane.spec_director.models import ActiveCampaigns
from control_plane.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)

_STATE_MANAGER = CampaignStateManager()


def is_suppressed(
    proposal_title: str,
    proposal_paths: list[str],
    active_campaigns: ActiveCampaigns | None = None,
) -> bool:
    """Return True if any active spec campaign covers the proposal's area.

    Fail-open: if loading active campaigns raises an exception, returns False
    and logs a warning rather than blocking proposal creation.
    """
    try:
        if active_campaigns is None:
            active_campaigns = _STATE_MANAGER.load()
    except Exception as exc:
        logger.warning('{"event": "spec_suppressor_read_error", "error": "%s"}', str(exc))
        return False

    text = proposal_title.lower()
    lower_paths = [p.lower() for p in proposal_paths]

    for campaign in active_campaigns.active_campaigns():
        for keyword in campaign.area_keywords:
            kw = keyword.lower()
            if kw in text:
                return True
            if any(kw in p for p in lower_paths):
                return True
    return False
