# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
# tests/spec_director/test_suppressor.py
from __future__ import annotations
from pathlib import Path
from operations_center.spec_director.models import CampaignRecord


def _write_spec(specs_dir: Path, slug: str, keywords: list[str]) -> Path:
    spec_path = specs_dir / f"{slug}.md"
    kw_lines = "".join(f"  - {kw}\n" for kw in keywords)
    spec_path.write_text(
        f"---\ncampaign_id: abc\nslug: {slug}\nphases: [implement]\nrepos: [repo_a]\n"
        f"area_keywords:\n{kw_lines}status: active\ncreated_at: 2026-04-15T00:00:00\n---\n"
    )
    return spec_path


def _active(keywords: list[str], tmp_path: Path) -> tuple[list[CampaignRecord], Path]:
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    spec_path = _write_spec(specs_dir, "add-auth", keywords)
    record = CampaignRecord(
        campaign_id="abc", slug="add-auth", spec_file=str(spec_path),
        status="active", created_at="2026-04-15T00:00:00+00:00",
    )
    return [record], specs_dir


def test_suppressed_by_path_keyword(tmp_path):
    from operations_center.spec_director.suppressor import is_suppressed
    campaigns, specs_dir = _active(["src/auth/"], tmp_path)
    assert is_suppressed("Fix auth login", ["src/auth/session.py"], campaigns, specs_dir) is True


def test_suppressed_by_title_keyword(tmp_path):
    from operations_center.spec_director.suppressor import is_suppressed
    campaigns, specs_dir = _active(["authentication"], tmp_path)
    assert is_suppressed("Improve authentication flow", [], campaigns, specs_dir) is True


def test_not_suppressed_unrelated(tmp_path):
    from operations_center.spec_director.suppressor import is_suppressed
    campaigns, specs_dir = _active(["src/auth/"], tmp_path)
    assert is_suppressed("Fix lint errors in src/reporting/", ["src/reporting/base.py"], campaigns, specs_dir) is False


def test_not_suppressed_no_active_campaigns():
    from operations_center.spec_director.suppressor import is_suppressed
    assert is_suppressed("Fix anything", ["src/auth/x.py"], [], None) is False


def test_suppressed_case_insensitive(tmp_path):
    from operations_center.spec_director.suppressor import is_suppressed
    campaigns, specs_dir = _active(["Authentication"], tmp_path)
    assert is_suppressed("improve authentication handler", [], campaigns, specs_dir) is True
