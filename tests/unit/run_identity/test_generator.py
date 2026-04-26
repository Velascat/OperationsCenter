"""Tests for run identity generation, ENV injection, and invocation preparation."""

from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from operations_center.run_identity.errors import RunIdentityEnvConflictError
from operations_center.run_identity.generator import (
    PreparedManagedAuditInvocation,
    apply_run_identity_env,
    generate_managed_run_identity,
    generate_managed_run_identity_from_config,
    prepare_managed_audit_invocation,
)
from operations_center.run_identity.models import is_valid_run_id

_CONFIG_DIR = Path(__file__).parent.parent.parent.parent / "config" / "managed_repos"
_NOW = datetime(2026, 4, 26, 16, 42, 33, tzinfo=timezone.utc)


class TestGenerateManagedRunIdentity:
    def test_run_id_contains_repo_id(self) -> None:
        identity = generate_managed_run_identity("videofoundry", "representative")
        assert "videofoundry" in identity.run_id

    def test_run_id_contains_audit_type(self) -> None:
        identity = generate_managed_run_identity("videofoundry", "representative")
        assert "representative" in identity.run_id

    def test_run_id_uses_utc_timestamp(self) -> None:
        identity = generate_managed_run_identity(
            "videofoundry", "representative", _now=_NOW
        )
        assert "20260426T164233Z" in identity.run_id

    def test_run_id_is_path_safe(self) -> None:
        # Uppercase T and Z are part of the ISO 8601 timestamp; both are path-safe.
        # Path-safety means no shell-special or filesystem-special chars.
        identity = generate_managed_run_identity("videofoundry", "representative")
        assert re.match(r"^[a-zA-Z0-9_]+$", identity.run_id), (
            f"run_id {identity.run_id!r} contains path-unsafe chars"
        )

    def test_run_id_is_json_safe(self) -> None:
        import json
        identity = generate_managed_run_identity("videofoundry", "representative")
        encoded = json.dumps({"run_id": identity.run_id})
        assert identity.run_id in encoded

    def test_run_id_passes_format_validator(self) -> None:
        identity = generate_managed_run_identity("videofoundry", "representative")
        assert is_valid_run_id(identity.run_id)

    def test_repeated_calls_differ(self) -> None:
        ids = {
            generate_managed_run_identity("videofoundry", "representative").run_id
            for _ in range(20)
        }
        assert len(ids) == 20, "Generated run_ids should be unique"

    def test_created_at_is_utc(self) -> None:
        identity = generate_managed_run_identity("videofoundry", "representative")
        assert identity.created_at.tzinfo is not None

    def test_created_at_fixed_by_now(self) -> None:
        identity = generate_managed_run_identity(
            "videofoundry", "representative", _now=_NOW
        )
        assert identity.created_at == _NOW

    def test_env_var_default_is_audit_run_id(self) -> None:
        identity = generate_managed_run_identity("videofoundry", "representative")
        assert identity.env_var == "AUDIT_RUN_ID"

    def test_metadata_forwarded(self) -> None:
        identity = generate_managed_run_identity(
            "videofoundry", "representative", metadata={"channel": "test"}
        )
        assert identity.metadata["channel"] == "test"

    def test_stack_authoring_in_run_id(self) -> None:
        identity = generate_managed_run_identity("videofoundry", "stack_authoring")
        assert "stack_authoring" in identity.run_id
        assert is_valid_run_id(identity.run_id)


class TestGenerateFromConfig:
    def test_env_var_from_config(self) -> None:
        identity = generate_managed_run_identity_from_config(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert identity.env_var == "AUDIT_RUN_ID"

    def test_identity_fields_set(self) -> None:
        identity = generate_managed_run_identity_from_config(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert identity.repo_id == "videofoundry"
        assert identity.audit_type == "representative"
        assert is_valid_run_id(identity.run_id)


class TestApplyRunIdentityEnv:
    def _make_identity(self, run_id: str = "videofoundry_representative_20260426T164233Z_a1b2c3d4"):
        from operations_center.run_identity.models import ManagedRunIdentity
        return ManagedRunIdentity(
            repo_id="videofoundry",
            audit_type="representative",
            run_id=run_id,
            created_at=_NOW,
        )

    def test_injects_audit_run_id(self) -> None:
        identity = self._make_identity()
        result = apply_run_identity_env({}, identity)
        assert result["AUDIT_RUN_ID"] == identity.run_id

    def test_preserves_existing_env(self) -> None:
        identity = self._make_identity()
        base = {"MY_VAR": "hello", "OTHER": "world"}
        result = apply_run_identity_env(base, identity)
        assert result["MY_VAR"] == "hello"
        assert result["OTHER"] == "world"

    def test_does_not_mutate_input(self) -> None:
        identity = self._make_identity()
        base = {"MY_VAR": "hello"}
        original = dict(base)
        apply_run_identity_env(base, identity)
        assert base == original

    def test_rejects_conflicting_audit_run_id(self) -> None:
        identity = self._make_identity()
        base = {"AUDIT_RUN_ID": "different_value_that_should_conflict"}
        with pytest.raises(RunIdentityEnvConflictError, match="conflicts"):
            apply_run_identity_env(base, identity)

    def test_allows_same_audit_run_id_by_default(self) -> None:
        identity = self._make_identity()
        base = {"AUDIT_RUN_ID": identity.run_id}
        result = apply_run_identity_env(base, identity)
        assert result["AUDIT_RUN_ID"] == identity.run_id

    def test_rejects_same_when_allow_same_false(self) -> None:
        identity = self._make_identity()
        base = {"AUDIT_RUN_ID": identity.run_id}
        with pytest.raises(RunIdentityEnvConflictError):
            apply_run_identity_env(base, identity, allow_same=False)

    def test_result_is_new_dict(self) -> None:
        identity = self._make_identity()
        base: dict[str, str] = {}
        result = apply_run_identity_env(base, identity)
        assert result is not base


class TestPrepareManagedAuditInvocation:
    def test_returns_prepared_invocation(self) -> None:
        result = prepare_managed_audit_invocation(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert isinstance(result, PreparedManagedAuditInvocation)

    def test_identity_present(self) -> None:
        result = prepare_managed_audit_invocation(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert result.identity.repo_id == "videofoundry"
        assert result.identity.audit_type == "representative"

    def test_request_present(self) -> None:
        result = prepare_managed_audit_invocation(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert result.request.repo_id == "videofoundry"
        assert result.request.audit_type == "representative"

    def test_request_contains_audit_run_id(self) -> None:
        result = prepare_managed_audit_invocation(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert "AUDIT_RUN_ID" in result.request.env
        assert result.request.env["AUDIT_RUN_ID"] == result.identity.run_id

    def test_request_run_id_matches_identity(self) -> None:
        result = prepare_managed_audit_invocation(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert result.request.run_id == result.identity.run_id

    def test_does_not_execute_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess
        calls: list = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
        prepare_managed_audit_invocation(
            "videofoundry", "representative", config_dir=_CONFIG_DIR
        )
        assert calls == [], "prepare_managed_audit_invocation must not execute commands"

    def test_works_for_all_six_audit_types(self) -> None:
        for at in ("representative", "enrichment", "ideation", "render", "segmentation", "stack_authoring"):
            result = prepare_managed_audit_invocation(
                "videofoundry", at, config_dir=_CONFIG_DIR
            )
            assert result.identity.audit_type == at

    def test_extra_env_preserved(self) -> None:
        result = prepare_managed_audit_invocation(
            "videofoundry",
            "representative",
            config_dir=_CONFIG_DIR,
            extra_env={"MY_VAR": "hello"},
        )
        assert result.request.env["MY_VAR"] == "hello"

    def test_metadata_forwarded(self) -> None:
        result = prepare_managed_audit_invocation(
            "videofoundry",
            "representative",
            config_dir=_CONFIG_DIR,
            metadata={"channel_slug": "test"},
        )
        assert result.identity.metadata["channel_slug"] == "test"


class TestBoundaryEnforcement:
    def test_no_videofoundry_imports_in_run_identity_module(self) -> None:
        src = Path(__file__).parent.parent.parent.parent / "src" / "operations_center" / "run_identity"
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
