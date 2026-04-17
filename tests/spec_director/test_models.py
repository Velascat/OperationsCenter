from __future__ import annotations
import pytest
from control_plane.spec_director.models import (
    CampaignRecord, ActiveCampaigns, ComplianceVerdict,
    SpecFrontMatter, TriggerSource,
)


def test_campaign_record_defaults():
    r = CampaignRecord(
        campaign_id="abc-123",
        slug="add-auth",
        spec_file="docs/specs/add-auth.md",
        status="active",
        created_at="2026-04-15T00:00:00+00:00",
    )
    assert r.status == "active"
    assert r.campaign_id == "abc-123"


def test_active_campaigns_active_only():
    ac = ActiveCampaigns(campaigns=[
        CampaignRecord(campaign_id="1", slug="a", spec_file="docs/specs/a.md",
                       status="active", created_at="2026-01-01T00:00:00+00:00"),
        CampaignRecord(campaign_id="2", slug="b", spec_file="docs/specs/b.md",
                       status="complete", created_at="2026-01-01T00:00:00+00:00"),
    ])
    assert len(ac.active_campaigns()) == 1
    assert ac.active_campaigns()[0].campaign_id == "1"


def test_compliance_verdict_fields():
    v = ComplianceVerdict(
        verdict="LGTM",
        spec_coverage=0.9,
        violations=[],
        notes="looks good",
        model="claude-sonnet-4-6",
        prompt_tokens=100,
        completion_tokens=50,
    )
    assert v.verdict == "LGTM"


def test_spec_front_matter_parse():
    raw = """---
campaign_id: abc-123
slug: add-auth
phases:
  - implement
  - test
repos:
  - MyRepo
area_keywords:
  - src/auth/
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Title
body text
"""
    fm = SpecFrontMatter.from_spec_text(raw)
    assert fm.campaign_id == "abc-123"
    assert "implement" in fm.phases
    assert fm.status == "active"


def test_trigger_source_values():
    assert TriggerSource.DROP_FILE == "drop_file"
    assert TriggerSource.PLANE_LABEL == "plane_label"
    assert TriggerSource.QUEUE_DRAIN == "queue_drain"


def test_spec_director_settings_defaults():
    from control_plane.config.settings import SpecDirectorSettings
    s = SpecDirectorSettings()
    assert s.enabled is True
    assert s.poll_interval_seconds == 120
    assert s.spec_trigger_queue_threshold == 5
    assert s.max_tasks_per_campaign == 6
    assert s.spec_retention_days == 90
    assert s.campaign_abandon_hours == 72
    assert s.compliance_diff_max_kb == 32
    assert s.brainstorm_context_snapshot_kb == 8


def test_spec_front_matter_missing_close_delimiter():
    from control_plane.spec_director.models import SpecFrontMatter
    with pytest.raises(ValueError, match="missing closing"):
        SpecFrontMatter.from_spec_text("---\ncampaign_id: abc\n# no closing delimiter")


def test_spec_front_matter_yaml_date_normalized():
    from control_plane.spec_director.models import SpecFrontMatter
    raw = "---\ncampaign_id: abc-123\nslug: test\ncreated_at: 2026-04-15\n---\n# Title\n"
    fm = SpecFrontMatter.from_spec_text(raw)
    assert isinstance(fm.created_at, str)
    assert "2026-04-15" in fm.created_at
