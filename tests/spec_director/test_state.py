# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
# tests/spec_director/test_state.py
from __future__ import annotations
from operations_center.spec_director.models import CampaignRecord, ActiveCampaigns


def test_load_returns_empty_when_missing(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    mgr = CampaignStateManager(state_path=tmp_path / "active.json")
    ac = mgr.load()
    assert ac.campaigns == []


def test_save_and_load_roundtrip(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    mgr = CampaignStateManager(state_path=tmp_path / "active.json")
    record = CampaignRecord(
        campaign_id="abc", slug="test", spec_file="docs/specs/test.md",
        status="active", created_at="2026-04-15T00:00:00+00:00",
    )
    mgr.save(ActiveCampaigns(campaigns=[record]))
    loaded = mgr.load()
    assert loaded.campaigns[0].campaign_id == "abc"


def test_corrupt_file_returns_empty_and_renames(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    p = tmp_path / "active.json"
    p.write_text("not json {{{")
    mgr = CampaignStateManager(state_path=p)
    ac = mgr.load()
    assert ac.campaigns == []
    corrupt_files = list(tmp_path.glob("active.json.corrupt.*"))
    assert len(corrupt_files) == 1


def test_mark_complete(tmp_path):
    from operations_center.spec_director.state import CampaignStateManager
    mgr = CampaignStateManager(state_path=tmp_path / "active.json")
    record = CampaignRecord(
        campaign_id="abc", slug="test", spec_file="docs/specs/test.md",
        status="active", created_at="2026-04-15T00:00:00+00:00",
    )
    mgr.save(ActiveCampaigns(campaigns=[record]))
    mgr.mark_complete("abc")
    loaded = mgr.load()
    assert loaded.campaigns[0].status == "complete"
