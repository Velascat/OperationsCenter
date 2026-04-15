# tests/spec_director/test_suppressor.py
from __future__ import annotations
from pathlib import Path
from control_plane.spec_director.models import ActiveCampaigns, CampaignRecord


def _active(keywords: list[str]) -> ActiveCampaigns:
    return ActiveCampaigns(campaigns=[
        CampaignRecord(
            campaign_id="abc", slug="add-auth", spec_file="docs/specs/add-auth.md",
            area_keywords=keywords, status="active",
            created_at="2026-04-15T00:00:00+00:00",
        )
    ])


def test_suppressed_by_path_keyword():
    from control_plane.spec_director.suppressor import is_suppressed
    ac = _active(["src/auth/"])
    assert is_suppressed("Fix auth login", ["src/auth/session.py"], ac) is True


def test_suppressed_by_title_keyword():
    from control_plane.spec_director.suppressor import is_suppressed
    ac = _active(["authentication"])
    assert is_suppressed("Improve authentication flow", [], ac) is True


def test_not_suppressed_unrelated():
    from control_plane.spec_director.suppressor import is_suppressed
    ac = _active(["src/auth/"])
    assert is_suppressed("Fix lint errors in src/reporting/", ["src/reporting/base.py"], ac) is False


def test_not_suppressed_no_active_campaigns():
    from control_plane.spec_director.suppressor import is_suppressed
    ac = ActiveCampaigns(campaigns=[])
    assert is_suppressed("Fix anything", ["src/auth/x.py"], ac) is False


def test_suppressed_case_insensitive():
    from control_plane.spec_director.suppressor import is_suppressed
    ac = _active(["Authentication"])
    assert is_suppressed("improve authentication handler", [], ac) is True
