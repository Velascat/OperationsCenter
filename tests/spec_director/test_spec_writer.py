# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
# tests/spec_director/test_spec_writer.py
from __future__ import annotations


_SPEC = """---
campaign_id: abc-123
slug: add-auth
phases: [implement, test]
repos: [MyRepo]
area_keywords: [src/auth/]
status: active
created_at: 2026-04-15T00:00:00+00:00
---
# Add Auth
"""


def test_writes_spec_to_canonical_path(tmp_path):
    from operations_center.spec_director.spec_writer import SpecWriter
    writer = SpecWriter(specs_dir=tmp_path / "docs/specs")
    path = writer.write(slug="add-auth", spec_text=_SPEC)
    assert path.exists()
    assert path.name == "add-auth.md"
    assert path.read_text() == _SPEC


def test_copies_to_workspace(tmp_path):
    from operations_center.spec_director.spec_writer import SpecWriter
    writer = SpecWriter(specs_dir=tmp_path / "docs/specs")
    workspace = tmp_path / "workspace/MyRepo"
    workspace.mkdir(parents=True)
    writer.write(slug="add-auth", spec_text=_SPEC, workspace_path=workspace)
    workspace_copy = workspace / "docs/specs/add-auth.md"
    assert workspace_copy.exists()
    assert workspace_copy.read_text() == _SPEC


def test_archive_old_specs(tmp_path):
    from operations_center.spec_director.spec_writer import SpecWriter
    specs_dir = tmp_path / "docs/specs"
    specs_dir.mkdir(parents=True)
    old_spec = specs_dir / "old-campaign.md"
    old_spec_text = """---
campaign_id: old-1
slug: old-campaign
phases: [implement]
repos: [MyRepo]
area_keywords: []
status: complete
created_at: 2020-01-01T00:00:00+00:00
---
# Old
"""
    old_spec.write_text(old_spec_text)
    writer = SpecWriter(specs_dir=specs_dir)
    writer.archive_expired(retention_days=1)
    archive_dir = specs_dir / "archive"
    assert (archive_dir / "old-campaign.md").exists()
    assert not old_spec.exists()
