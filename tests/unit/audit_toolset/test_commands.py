# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Tests for command resolution from managed repo config."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from operations_center.audit_toolset.commands import resolve_invocation_request
from operations_center.audit_toolset.errors import (
    ManagedAuditCommandUnavailableError,
    ManagedAuditTypeUnsupportedError,
    ManagedRepoCapabilityError,
    ManagedRepoNotFoundError,
)

_RUN_ID = "aabbccdd11223344aabbccdd11223344"
_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config" / "managed_repos"


class TestResolverLoadsConfig:
    def test_loads_videofoundry_config(self) -> None:
        req = resolve_invocation_request(
            "videofoundry", "representative", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert req.repo_id == "videofoundry"

    def test_raises_for_unknown_repo(self) -> None:
        with pytest.raises(ManagedRepoNotFoundError):
            resolve_invocation_request(
                "no_such_repo", "representative", _RUN_ID, config_dir=_CONFIG_DIR
            )


class TestCapabilityVerification:
    def test_videofoundry_has_audit_capability(self) -> None:
        req = resolve_invocation_request(
            "videofoundry", "representative", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert req.audit_type == "representative"

    def test_raises_if_capability_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "no_audit.yaml"
        cfg.write_text(
            "repo_id: no_audit\n"
            "repo_name: No Audit\n"
            "repo_root: ./no_audit\n"
            "run_id:\n"
            "  source: operations_center\n"
            "  env_var: AUDIT_RUN_ID\n"
            "capabilities: []\n"
        )
        with pytest.raises(ManagedRepoCapabilityError):
            resolve_invocation_request(
                "no_audit", "representative", _RUN_ID, config_dir=tmp_path
            )


class TestAuditTypeVerification:
    def test_accepts_representative(self) -> None:
        req = resolve_invocation_request(
            "videofoundry", "representative", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert req.audit_type == "representative"

    def test_raises_for_unsupported_type(self) -> None:
        with pytest.raises(ManagedAuditTypeUnsupportedError):
            resolve_invocation_request(
                "videofoundry", "nonexistent_type", _RUN_ID, config_dir=_CONFIG_DIR
            )

    def test_all_six_vf_audit_types_known(self) -> None:
        expected = {
            "representative", "enrichment", "ideation",
            "render", "segmentation", "stack_authoring",
        }
        for at in expected:
            req = resolve_invocation_request(
                "videofoundry", at, _RUN_ID, config_dir=_CONFIG_DIR
            )
            assert req.audit_type == at


class TestCommandStatusPolicy:
    def test_verified_always_allowed(self) -> None:
        # representative is command_status: verified
        req = resolve_invocation_request(
            "videofoundry", "representative", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert req.command is not None

    def test_not_yet_run_allowed_by_default(self) -> None:
        # enrichment and others are not_yet_run
        req = resolve_invocation_request(
            "videofoundry", "enrichment", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert req.audit_type == "enrichment"

    def test_not_yet_run_blocked_when_strict(self) -> None:
        with pytest.raises(ManagedAuditCommandUnavailableError, match="not_yet_run"):
            resolve_invocation_request(
                "videofoundry",
                "enrichment",
                _RUN_ID,
                config_dir=_CONFIG_DIR,
                allow_not_yet_run=False,
            )

    def test_unknown_status_blocked(self, tmp_path: Path) -> None:
        cfg = tmp_path / "repo_unknown.yaml"
        cfg.write_text(
            "repo_id: repo_unknown\n"
            "repo_name: Repo Unknown\n"
            "repo_root: ./repo_unknown\n"
            "run_id:\n"
            "  source: operations_center\n"
            "  env_var: AUDIT_RUN_ID\n"
            "capabilities: [audit]\n"
            "audit:\n"
            "  output_discovery:\n"
            "    entry_point: run_status.json\n"
            "  audit_types:\n"
            "    - audit_type: experimental\n"
            "      command: python -m tools.audit.cli.run_experimental\n"
            "      command_status: unknown\n"
            "      working_dir: .\n"
            "      output_dir: tools/audit/report/experimental\n"
            "      status_file: run_status.json\n"
            "      run_status_finalization: false\n"
        )
        with pytest.raises(ManagedAuditCommandUnavailableError, match="unknown"):
            resolve_invocation_request(
                "repo_unknown", "experimental", _RUN_ID, config_dir=tmp_path
            )

    def test_needs_confirmation_blocked(self, tmp_path: Path) -> None:
        cfg = tmp_path / "repo_confirm.yaml"
        cfg.write_text(
            "repo_id: repo_confirm\n"
            "repo_name: Repo Confirm\n"
            "repo_root: ./repo_confirm\n"
            "run_id:\n"
            "  source: operations_center\n"
            "  env_var: AUDIT_RUN_ID\n"
            "capabilities: [audit]\n"
            "audit:\n"
            "  output_discovery:\n"
            "    entry_point: run_status.json\n"
            "  audit_types:\n"
            "    - audit_type: draft\n"
            "      command: python -m tools.audit.cli.run_draft\n"
            "      command_status: needs_confirmation\n"
            "      working_dir: .\n"
            "      output_dir: tools/audit/report/draft\n"
            "      status_file: run_status.json\n"
            "      run_status_finalization: false\n"
        )
        with pytest.raises(ManagedAuditCommandUnavailableError, match="needs_confirmation"):
            resolve_invocation_request(
                "repo_confirm", "draft", _RUN_ID, config_dir=tmp_path
            )


class TestRunIdInjection:
    def test_audit_run_id_in_env(self) -> None:
        req = resolve_invocation_request(
            "videofoundry", "representative", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert "AUDIT_RUN_ID" in req.env
        assert req.env["AUDIT_RUN_ID"] == _RUN_ID

    def test_run_id_matches_env(self) -> None:
        req = resolve_invocation_request(
            "videofoundry", "representative", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert req.run_id == req.env["AUDIT_RUN_ID"]

    def test_extra_env_merged(self) -> None:
        req = resolve_invocation_request(
            "videofoundry",
            "representative",
            _RUN_ID,
            config_dir=_CONFIG_DIR,
            extra_env={"MY_CUSTOM_VAR": "hello"},
        )
        assert req.env["MY_CUSTOM_VAR"] == "hello"
        assert req.env["AUDIT_RUN_ID"] == _RUN_ID


class TestOutputDir:
    def test_expected_output_dir_set(self) -> None:
        req = resolve_invocation_request(
            "videofoundry", "representative", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert req.expected_output_dir
        assert "representative" in req.expected_output_dir

    def test_stack_authoring_output_dir_is_authoring(self) -> None:
        req = resolve_invocation_request(
            "videofoundry", "stack_authoring", _RUN_ID, config_dir=_CONFIG_DIR
        )
        assert "authoring" in req.expected_output_dir
        assert "stack_authoring" not in req.expected_output_dir


class TestBoundaryEnforcement:
    def test_no_videofoundry_imports_in_commands_module(self) -> None:
        src = Path(__file__).parent.parent.parent.parent / "src" / "operations_center" / "audit_toolset"
        for py_file in src.rglob("*.py"):
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not node.module.startswith("tools.audit"), (
                        f"{py_file}: imports VideoFoundry code: {node.module}"
                    )
                    assert not node.module.startswith("workflow."), (
                        f"{py_file}: imports VideoFoundry workflow code: {node.module}"
                    )
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("tools.audit"), (
                            f"{py_file}: imports VideoFoundry code: {alias.name}"
                        )
