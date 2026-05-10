# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the example managed-repo config template.

The public repo ships an `example_managed_repo.yaml` template that
operators copy to `config/managed_repos/local/<repo_id>.yaml` and edit
to bind OperationsCenter to a specific managed repo. These tests
verify the template parses cleanly and exercises every loader code
path — they are config-only and do not invoke any managed-repo
commands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.managed_repos import load_managed_repo_config
from operations_center.managed_repos.models import ManagedRepoConfig

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config" / "managed_repos"
_EXPECTED_AUDIT_TYPES = {"audit_type_1", "audit_type_2"}


@pytest.fixture(scope="module")
def example_config() -> ManagedRepoConfig:
    return load_managed_repo_config("example_managed_repo", config_dir=_CONFIG_DIR)


class TestBasicStructure:
    def test_parses_without_error(self, example_config: ManagedRepoConfig) -> None:
        assert example_config is not None

    def test_repo_id_matches_template(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.repo_id == "example_managed_repo"

    def test_repo_name_present(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.repo_name

    def test_repo_root_present(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.repo_root


class TestAuditCapability:
    def test_audit_capability_declared(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.has_capability("audit")

    def test_audit_block_present(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.audit is not None

    def test_template_audit_types_present(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.audit is not None
        declared = set(example_config.audit_type_names)
        assert declared == _EXPECTED_AUDIT_TYPES, (
            f"Missing: {_EXPECTED_AUDIT_TYPES - declared}, "
            f"Extra: {declared - _EXPECTED_AUDIT_TYPES}"
        )

    def test_each_audit_type_has_command(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.audit is not None
        for at in example_config.audit.audit_types:
            assert at.command, f"{at.audit_type}: command is empty"

    def test_each_command_is_external_invocation_not_import(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.audit is not None
        for at in example_config.audit.audit_types:
            assert not at.command.startswith("import "), (
                f"{at.audit_type}: command looks like a Python import, not an invocation"
            )
            assert not at.command.startswith("from "), (
                f"{at.audit_type}: command looks like a Python import, not an invocation"
            )

    def test_each_audit_type_has_output_dir(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.audit is not None
        for at in example_config.audit.audit_types:
            assert at.output_dir, f"{at.audit_type}: output_dir is empty"

    def test_each_audit_type_has_status_file(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.audit is not None
        for at in example_config.audit.audit_types:
            assert at.status_file, f"{at.audit_type}: status_file is empty"

    def test_each_audit_type_has_command_status(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.audit is not None
        valid_statuses = {"verified", "not_yet_run", "unknown", "needs_confirmation"}
        for at in example_config.audit.audit_types:
            assert at.command_status in valid_statuses, (
                f"{at.audit_type}: command_status={at.command_status!r} not in {valid_statuses}"
            )


class TestRunIdInjection:
    def test_run_id_block_present(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.run_id is not None

    def test_run_id_source_is_operations_center(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.run_id.source == "operations_center"

    def test_audit_run_id_env_var_declared(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.run_id.env_var == "AUDIT_RUN_ID"

    def test_run_id_required_for_managed_runs(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.run_id.required_for_managed_runs is True

    def test_each_audit_type_injects_audit_run_id(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.audit is not None
        for at in example_config.audit.audit_types:
            assert "AUDIT_RUN_ID" in at.env_injected, (
                f"{at.audit_type}: AUDIT_RUN_ID not in env_injected"
            )


class TestOutputDiscovery:
    def test_output_discovery_entry_point_is_run_status(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        assert example_config.audit is not None
        assert example_config.audit.output_discovery.entry_point == "run_status.json"

    def test_manifest_discovery_status(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.audit is not None
        chain = example_config.audit.output_discovery.chain
        manifest_step = next(
            (s for s in chain if "artifact_manifest" in s.file),
            None,
        )
        assert manifest_step is not None, "No artifact_manifest step in discovery chain"
        assert manifest_step.status in ("planned", "not_yet_available")


class TestBoundaryPolicy:
    def test_boundary_policy_present(self, example_config: ManagedRepoConfig) -> None:
        assert example_config.boundary is not None

    def test_importing_managed_repo_is_forbidden(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        forbidden_text = " ".join(example_config.boundary.forbidden).lower()
        assert "import" in forbidden_text, (
            "Boundary policy must explicitly forbid importing the managed repo's code"
        )

    def test_invoke_commands_is_allowed(
        self, example_config: ManagedRepoConfig,
    ) -> None:
        allowed_text = " ".join(example_config.boundary.allowed).lower()
        assert "invoke" in allowed_text or "command" in allowed_text, (
            "Boundary policy must explicitly allow invoking the managed repo's commands"
        )


class TestLocalOverrideTakesPriority:
    def test_local_subdir_overrides_tracked_template(
        self, tmp_path: Path,
    ) -> None:
        base = tmp_path
        local = base / "local"
        local.mkdir()
        run_id_block = (
            "run_id:\n"
            "  source: operations_center\n"
            "  env_var: AUDIT_RUN_ID\n"
            "  format: uuid_hex\n"
            "  required_for_managed_runs: true\n"
        )
        (base / "tracked_repo.yaml").write_text(
            "repo_id: tracked_repo\n"
            "repo_name: TrackedRepo\n"
            "repo_root: ../TrackedRepo\n"
            "capabilities: []\n"
            + run_id_block,
            encoding="utf-8",
        )
        (local / "tracked_repo.yaml").write_text(
            "repo_id: tracked_repo\n"
            "repo_name: LocalOverride\n"
            "repo_root: ../LocalOverride\n"
            "capabilities: []\n"
            + run_id_block,
            encoding="utf-8",
        )
        cfg = load_managed_repo_config("tracked_repo", config_dir=base)
        assert cfg.repo_name == "LocalOverride", (
            "Loader must prefer local/{repo_id}.yaml over the tracked template"
        )
