# src/control_plane/spec_director/suppressor.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from control_plane.spec_director.models import CampaignRecord

logger = logging.getLogger(__name__)


def is_suppressed(
    proposal_title: str,
    proposal_paths: list[str],
    active_campaigns: list["CampaignRecord"] | None = None,
    specs_dir: Path | None = None,
) -> bool:
    """Return True if an active campaign covers the given proposal's area.

    area_keywords are loaded from each campaign's spec front matter.
    Falls back gracefully if the spec file is missing or unparseable.
    """
    if not active_campaigns:
        return False
    for campaign in active_campaigns:
        keywords = _load_area_keywords(campaign, specs_dir)
        if _any_keyword_matches(keywords, proposal_title, proposal_paths):
            logger.info(
                '{"event": "spec_suppressed", "campaign_id": "%s", "reason": "active_spec_campaign"}',
                campaign.campaign_id,
            )
            return True
    return False


def _load_area_keywords(campaign: "CampaignRecord", specs_dir: Path | None) -> list[str]:
    """Load area_keywords from the campaign's spec front matter."""
    spec_path = Path(campaign.spec_file)
    if specs_dir is not None and not spec_path.is_absolute():
        spec_path = specs_dir / spec_path.name
    try:
        from control_plane.spec_director.models import SpecFrontMatter
        text = spec_path.read_text(encoding="utf-8")
        fm = SpecFrontMatter.from_spec_text(text)
        return fm.area_keywords
    except Exception:
        return []


def _any_keyword_matches(
    keywords: list[str],
    title: str,
    paths: list[str],
) -> bool:
    if not keywords:
        return False
    title_lower = title.lower()
    paths_lower = [p.lower() for p in paths]
    for kw in keywords:
        kw_lower = kw.lower()
        if kw_lower in title_lower:
            return True
        if any(kw_lower in p for p in paths_lower):
            return True
    return False
