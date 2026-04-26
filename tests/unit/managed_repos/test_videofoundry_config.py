"""Tests for the VideoFoundry managed repo config.

These tests are config-only. They do not invoke VideoFoundry commands,
do not require a real audit output directory, and do not require Phase 2
manifest files. They verify the contract is well-formed and internally
consistent with Phase 0 ground truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.managed_repos import load_managed_repo_config
from operations_center.managed_repos.models import ManagedRepoConfig

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config" / "managed_repos"
_EXPECTED_AUDIT_TYPES = {
    "representative",
    "enrichment",
    "ideation",
    "render",
    "segmentation",
    "stack_authoring",
}


@pytest.fixture(scope="module")
def vf_config() -> ManagedRepoConfig:
    return load_managed_repo_config("videofoundry", config_dir=_CONFIG_DIR)


class TestBasicStructure:
    def test_parses_without_error(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config is not None

    def test_repo_id_is_videofoundry(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.repo_id == "videofoundry"

    def test_repo_name_present(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.repo_name

    def test_repo_root_present(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.repo_root


class TestAuditCapability:
    def test_audit_capability_declared(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.has_capability("audit")

    def test_audit_block_present(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None

    def test_all_six_audit_types_present(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        declared = set(vf_config.audit_type_names)
        assert declared == _EXPECTED_AUDIT_TYPES, (
            f"Missing: {_EXPECTED_AUDIT_TYPES - declared}, Extra: {declared - _EXPECTED_AUDIT_TYPES}"
        )

    def test_each_audit_type_has_command(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        for at in vf_config.audit.audit_types:
            assert at.command, f"{at.audit_type}: command is empty"

    def test_each_command_is_external_invocation_not_import(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        for at in vf_config.audit.audit_types:
            assert not at.command.startswith("import "), (
                f"{at.audit_type}: command looks like a Python import, not an invocation"
            )
            assert not at.command.startswith("from "), (
                f"{at.audit_type}: command looks like a Python import, not an invocation"
            )

    def test_each_audit_type_has_output_dir(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        for at in vf_config.audit.audit_types:
            assert at.output_dir, f"{at.audit_type}: output_dir is empty"

    def test_each_audit_type_has_status_file(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        for at in vf_config.audit.audit_types:
            assert at.status_file, f"{at.audit_type}: status_file is empty"

    def test_each_audit_type_has_command_status(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        valid_statuses = {"verified", "not_yet_run", "unknown", "needs_confirmation"}
        for at in vf_config.audit.audit_types:
            assert at.command_status in valid_statuses, (
                f"{at.audit_type}: command_status={at.command_status!r} not in {valid_statuses}"
            )


class TestRunIdInjection:
    def test_run_id_block_present(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.run_id is not None

    def test_run_id_source_is_operations_center(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.run_id.source == "operations_center"

    def test_audit_run_id_env_var_declared(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.run_id.env_var == "AUDIT_RUN_ID"

    def test_run_id_required_for_managed_runs(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.run_id.required_for_managed_runs is True

    def test_each_audit_type_injects_audit_run_id(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        for at in vf_config.audit.audit_types:
            assert "AUDIT_RUN_ID" in at.env_injected, (
                f"{at.audit_type}: AUDIT_RUN_ID not in env_injected"
            )


class TestOutputDiscovery:
    def test_output_discovery_entry_point_is_run_status(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        assert vf_config.audit.output_discovery.entry_point == "run_status.json"

    def test_manifest_discovery_not_yet_available(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        chain = vf_config.audit.output_discovery.chain
        manifest_step = next(
            (s for s in chain if "artifact_manifest" in s.file),
            None,
        )
        assert manifest_step is not None, "No artifact_manifest step in discovery chain"
        assert manifest_step.status in ("planned", "not_yet_available"), (
            f"artifact_manifest step claims status={manifest_step.status!r} — "
            "Phase 2+5 are not done; do not pretend manifest is available"
        )


class TestBoundaryPolicy:
    def test_boundary_policy_present(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.boundary is not None

    def test_importing_videofoundry_is_forbidden(self, vf_config: ManagedRepoConfig) -> None:
        forbidden_text = " ".join(vf_config.boundary.forbidden).lower()
        assert "import" in forbidden_text, (
            "Boundary policy must explicitly forbid importing VideoFoundry code"
        )

    def test_invoke_commands_is_allowed(self, vf_config: ManagedRepoConfig) -> None:
        allowed_text = " ".join(vf_config.boundary.allowed).lower()
        assert "invoke" in allowed_text or "command" in allowed_text, (
            "Boundary policy must explicitly allow invoking VideoFoundry commands"
        )


class TestPhase0GroundTruth:
    """Verify the config accurately reflects Phase 0 findings."""

    def test_representative_is_verified(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        rep = vf_config.audit.get_audit_type("representative")
        assert rep is not None
        assert rep.command_status == "verified"

    def test_representative_finalizes_run_status(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        rep = vf_config.audit.get_audit_type("representative")
        assert rep is not None
        assert rep.run_status_finalization is True

    def test_non_representative_do_not_finalize(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        non_rep = [
            at for at in vf_config.audit.audit_types
            if at.audit_type != "representative"
        ]
        for at in non_rep:
            assert at.run_status_finalization is False, (
                f"{at.audit_type}: claimed run_status_finalization=True but Phase 0 "
                "found no finalization logic for non-representative audits"
            )

    def test_stack_authoring_output_dir_is_authoring(self, vf_config: ManagedRepoConfig) -> None:
        assert vf_config.audit is not None
        sa = vf_config.audit.get_audit_type("stack_authoring")
        assert sa is not None
        # Phase 0 finding: directory is named "authoring", not "stack_authoring"
        assert "authoring" in sa.output_dir
        assert "stack_authoring" not in sa.output_dir
