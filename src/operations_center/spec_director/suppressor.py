# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
# src/operations_center/spec_director/suppressor.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from operations_center.spec_director.models import CampaignRecord

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
    # Resolve to an absolute path: if specs_dir is given, look up by filename
    # (specs live flat in specs_dir, never in subdirectories).
    # If no specs_dir, use the stored path as-is (may be relative to CWD).
    if specs_dir is not None:
        candidate = specs_dir / spec_path.name
        if candidate.exists():
            spec_path = candidate
        elif spec_path.is_absolute() and spec_path.exists():
            pass  # use the absolute path as stored
        else:
            # Try the stored path relative to specs_dir parent
            spec_path = candidate  # best guess; will fail gracefully below
    try:
        from operations_center.spec_director.models import SpecFrontMatter
        text = spec_path.read_text(encoding="utf-8")
        fm = SpecFrontMatter.from_spec_text(text)
        return fm.area_keywords
    except Exception as exc:
        logger.debug(
            '{"event": "spec_keywords_load_failed", "spec_file": "%s", "error": "%s"}',
            str(campaign.spec_file), str(exc),
        )
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
