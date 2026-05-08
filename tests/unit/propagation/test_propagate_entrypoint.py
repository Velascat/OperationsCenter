# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for operations-center-propagate (R5.2 + R5.3).

Settings block tests + entrypoint integration with --dry-run so we
don't need a real Plane.
"""
from __future__ import annotations

import json
from pathlib import Path


from operations_center.config.settings import (
    load_settings,
)
from operations_center.entrypoints.propagate.main import main


_BASE_YAML = """\
plane:
  base_url: http://localhost:8080
  api_token_env: PLANE_API_TOKEN
  workspace_slug: ws
  project_id: pid
git:
  token_env: GH_TOKEN
kodo:
  binary: kodo
repos:
  Demo:
    clone_url: git@example.com:demo.git
    default_branch: main
"""


def _write_config(tmp_path: Path, body: str = "") -> Path:
    cfg = tmp_path / "operations_center.yaml"
    cfg.write_text(_BASE_YAML + body, encoding="utf-8")
    return cfg


# ---------------------------------------------------------------------------
# Settings block — defaults + overrides
# ---------------------------------------------------------------------------


class TestSettingsBlock:
    def test_default_disabled(self, tmp_path: Path) -> None:
        cfg = _write_config(tmp_path)
        s = load_settings(cfg)
        assert s.contract_change_propagation.enabled is False
        assert s.contract_change_propagation.auto_trigger_edge_types == []
        assert s.contract_change_propagation.dedup_window_hours == 24

    def test_enable_and_set_edge_types(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\ncontract_change_propagation:\n"
            "  enabled: true\n"
            "  auto_trigger_edge_types:\n"
            "    - depends_on_contracts_from\n"
            "  dedup_window_hours: 12\n",
        )
        s = load_settings(cfg)
        assert s.contract_change_propagation.enabled is True
        assert s.contract_change_propagation.auto_trigger_edge_types == ["depends_on_contracts_from"]
        assert s.contract_change_propagation.dedup_window_hours == 12

    def test_pair_overrides_parsed(self, tmp_path: Path) -> None:
        cfg = _write_config(
            tmp_path,
            "\ncontract_change_propagation:\n"
            "  enabled: true\n"
            "  pair_overrides:\n"
            "    - target_repo_id: cxrp\n"
            "      consumer_repo_id: operations_center\n"
            "      action: ready_for_ai\n"
            "      reason: trusted\n",
        )
        s = load_settings(cfg)
        assert len(s.contract_change_propagation.pair_overrides) == 1
        ov = s.contract_change_propagation.pair_overrides[0]
        assert ov.target_repo_id == "cxrp"
        assert ov.action == "ready_for_ai"


# ---------------------------------------------------------------------------
# Entrypoint — argument parsing + dry-run
# ---------------------------------------------------------------------------


class TestEntrypointArgs:
    def test_missing_config_exits_two(self, tmp_path: Path, capsys) -> None:
        rc = main([
            "--target", "cxrp",
            "--version", "v1",
            "--config", str(tmp_path / "nope.yaml"),
        ])
        assert rc == 2
        err = capsys.readouterr().err
        assert "config not found" in err.lower()

    def test_require_enabled_with_disabled_config_exits_one(
        self, tmp_path: Path, capsys
    ) -> None:
        cfg = _write_config(tmp_path)  # disabled by default
        rc = main([
            "--target", "cxrp",
            "--version", "v1",
            "--config", str(cfg),
            "--require-enabled",
        ])
        assert rc == 1
        err = capsys.readouterr().err
        assert "enabled is False" in err


class TestDryRunHappyPath:
    def test_dry_run_with_disabled_writes_record_zero_outcomes(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _write_config(tmp_path)
        rc = main([
            "--target", "cxrp",
            "--version", "v1",
            "--config", str(cfg),
            "--dry-run",
            "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        # Disabled policy → all outcomes are skip
        assert payload["target_repo_id"] == "cxrp"
        assert payload["target_canonical"] == "CxRP"
        assert all(o["decision_action"] == "skip" for o in payload["outcomes"])
        # Record artifact written under default record_dir (state/propagation/)
        record_dir = tmp_path / "state" / "propagation"
        assert record_dir.exists()
        assert any(record_dir.glob("*.json"))

    def test_dry_run_with_enabled_creates_synthetic_issues(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _write_config(
            tmp_path,
            "\ncontract_change_propagation:\n"
            "  enabled: true\n"
            "  auto_trigger_edge_types:\n"
            "    - depends_on_contracts_from\n",
        )
        rc = main([
            "--target", "cxrp",
            "--version", "v1",
            "--config", str(cfg),
            "--dry-run",
            "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        # All consumers got synthetic DRY-RUN issue ids
        for o in payload["outcomes"]:
            assert o["issue_id"] is not None
            assert o["issue_id"].startswith("DRY-RUN-")
            assert o["decision_action"] == "backlog"

    def test_dry_run_human_output(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _write_config(tmp_path)
        rc = main([
            "--target", "cxrp",
            "--version", "v1",
            "--config", str(cfg),
            "--dry-run",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "propagation run:" in out
        assert "CxRP" in out


class TestUnknownTargetGracefulRecord:
    def test_unknown_target_writes_record_exits_zero(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cfg = _write_config(tmp_path)
        rc = main([
            "--target", "ghost-repo",
            "--version", "v1",
            "--config", str(cfg),
            "--dry-run",
            "--json",
        ])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["target_canonical"] == "(unknown)"
        assert payload["outcomes"] == []
