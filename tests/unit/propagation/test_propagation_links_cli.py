# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for operations-center-propagation-links (R5.5)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


from operations_center.entrypoints.propagation_links.main import main


def _write_record(
    records_dir: Path,
    *,
    run_id: str,
    target_repo_id: str,
    target_canonical: str,
    target_version: str,
    triggered_at: datetime,
    outcomes: list[dict] | None = None,
) -> Path:
    records_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "propagator_run_id": run_id,
        "target_repo_id": target_repo_id,
        "target_canonical": target_canonical,
        "target_version": target_version,
        "triggered_at": triggered_at.isoformat(),
        "policy_summary": {"enabled": True, "auto_trigger_edge_types": ["depends_on_contracts_from"], "dedup_window_hours": 24},
        "outcomes": outcomes or [],
        "impact_summary": {"affected_count": len(outcomes or []), "public_affected": [], "private_affected": []},
    }
    p = records_dir / f"{run_id}.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


class TestList:
    def test_no_records_exits_one(self, tmp_path: Path, capsys) -> None:
        rc = main(["--records-dir", str(tmp_path / "empty"), "list"])
        assert rc == 1

    def test_lists_in_chronological_order_newest_first(
        self, tmp_path: Path, capsys
    ) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="aaa", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 7, tzinfo=timezone.utc))
        _write_record(rd, run_id="bbb", target_repo_id="rxp", target_canonical="RxP",
                      target_version="v2", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "list"])
        assert rc == 0
        out = capsys.readouterr().out
        # Newest (May 8 / RxP) appears before older (May 7 / CxRP)
        rxp_pos = out.find("RxP")
        cxrp_pos = out.find("CxRP")
        assert rxp_pos < cxrp_pos
        assert rxp_pos != -1


class TestShow:
    def test_show_by_full_run_id(self, tmp_path: Path, capsys) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="abc123def456", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "--json", "show", "abc123def456"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["propagator_run_id"] == "abc123def456"

    def test_show_by_prefix(self, tmp_path: Path, capsys) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="abc123def456", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "show", "abc12"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "abc123def456" in out

    def test_show_ambiguous_prefix_exits_one(self, tmp_path: Path, capsys) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="abc111", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        _write_record(rd, run_id="abc222", target_repo_id="rxp", target_canonical="RxP",
                      target_version="v2", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "show", "abc"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "ambiguous" in out.lower()

    def test_show_missing_run_id_exits_one(self, tmp_path: Path, capsys) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="abc", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "show", "ghost"])
        assert rc == 1


class TestLatest:
    def test_latest_picks_newest_for_target(self, tmp_path: Path, capsys) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="old", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 7, tzinfo=timezone.utc))
        _write_record(rd, run_id="new", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v2", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        _write_record(rd, run_id="other", target_repo_id="rxp", target_canonical="RxP",
                      target_version="v9", triggered_at=datetime(2026, 5, 8, 12, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "--json", "latest", "--target", "cxrp"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["propagator_run_id"] == "new"

    def test_latest_canonical_name_also_works(self, tmp_path: Path, capsys) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="r1", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "--json", "latest", "--target", "CxRP"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["propagator_run_id"] == "r1"

    def test_latest_no_match_exits_one(self, tmp_path: Path, capsys) -> None:
        rd = tmp_path / "records"
        _write_record(rd, run_id="r1", target_repo_id="cxrp", target_canonical="CxRP",
                      target_version="v1", triggered_at=datetime(2026, 5, 8, tzinfo=timezone.utc))
        rc = main(["--records-dir", str(rd), "latest", "--target", "ghost"])
        assert rc == 1
