# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for ManagedRunIdentity model and run_id format validation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from operations_center.run_identity.models import ManagedRunIdentity, is_valid_run_id

_NOW = datetime(2026, 4, 26, 16, 42, 33, tzinfo=timezone.utc)

_VALID_RUN_ID = "example_managed_repo_audit_type_1_20260426T164233Z_a1b2c3d4"

_MINIMAL = {
    "repo_id": "example_managed_repo",
    "audit_type": "audit_type_1",
    "run_id": _VALID_RUN_ID,
    "created_at": _NOW.isoformat(),
}


class TestIsValidRunId:
    def test_valid_run_id(self) -> None:
        assert is_valid_run_id(_VALID_RUN_ID)

    def test_stack_authoring_with_underscore_in_type(self) -> None:
        assert is_valid_run_id("example_managed_repo_audit_type_2_20260426T164233Z_b5c6d7e8")

    def test_rejects_missing_suffix(self) -> None:
        assert not is_valid_run_id("example_managed_repo_audit_type_1_20260426T164233Z")

    def test_rejects_missing_timestamp(self) -> None:
        assert not is_valid_run_id("example_managed_repo_audit_type_1_a1b2c3d4")

    def test_rejects_empty_string(self) -> None:
        assert not is_valid_run_id("")

    def test_rejects_uuid_hex_only(self) -> None:
        assert not is_valid_run_id("3dead998d4c44e1cb296bef061de50f3")

    def test_rejects_uppercase(self) -> None:
        assert not is_valid_run_id("ExampleManagedRepo_audit_type_1_20260426T164233Z_a1b2c3d4")

    def test_rejects_dashes(self) -> None:
        assert not is_valid_run_id("example_managed_repo-audit_type_1-20260426T164233Z-a1b2c3d4")

    def test_rejects_suffix_too_long(self) -> None:
        assert not is_valid_run_id("example_managed_repo_audit_type_1_20260426T164233Z_a1b2c3d4e5")

    def test_rejects_suffix_too_short(self) -> None:
        assert not is_valid_run_id("example_managed_repo_audit_type_1_20260426T164233Z_a1b2c3")


class TestManagedRunIdentity:
    def test_parses_minimal(self) -> None:
        m = ManagedRunIdentity.model_validate(_MINIMAL)
        assert m.repo_id == "example_managed_repo"
        assert m.audit_type == "audit_type_1"
        assert m.run_id == _VALID_RUN_ID

    def test_env_var_defaults_to_audit_run_id(self) -> None:
        m = ManagedRunIdentity.model_validate(_MINIMAL)
        assert m.env_var == "AUDIT_RUN_ID"

    def test_metadata_defaults_empty(self) -> None:
        m = ManagedRunIdentity.model_validate(_MINIMAL)
        assert m.metadata == {}

    def test_metadata_accepted(self) -> None:
        data = {**_MINIMAL, "metadata": {"channel_slug": "Connective_Contours"}}
        m = ManagedRunIdentity.model_validate(data)
        assert m.metadata["channel_slug"] == "Connective_Contours"

    def test_rejects_empty_repo_id(self) -> None:
        with pytest.raises(ValidationError, match="repo_id"):
            ManagedRunIdentity.model_validate({**_MINIMAL, "repo_id": ""})

    def test_rejects_empty_audit_type(self) -> None:
        with pytest.raises(ValidationError, match="audit_type"):
            ManagedRunIdentity.model_validate({**_MINIMAL, "audit_type": ""})

    def test_rejects_invalid_run_id_format(self) -> None:
        with pytest.raises(ValidationError, match="run_id"):
            ManagedRunIdentity.model_validate({**_MINIMAL, "run_id": "not_a_valid_run_id"})

    def test_rejects_plain_uuid_as_run_id(self) -> None:
        with pytest.raises(ValidationError):
            ManagedRunIdentity.model_validate(
                {**_MINIMAL, "run_id": "3dead998d4c44e1cb296bef061de50f3"}
            )

    def test_rejects_naive_datetime(self) -> None:
        naive = datetime(2026, 4, 26, 16, 42, 33)  # no tzinfo
        with pytest.raises(ValidationError, match="timezone"):
            ManagedRunIdentity.model_validate({**_MINIMAL, "created_at": naive})

    def test_accepts_utc_datetime(self) -> None:
        m = ManagedRunIdentity.model_validate(_MINIMAL)
        assert m.created_at.tzinfo is not None

    def test_custom_env_var(self) -> None:
        data = {**_MINIMAL, "env_var": "MY_AUDIT_RUN_ID"}
        m = ManagedRunIdentity.model_validate(data)
        assert m.env_var == "MY_AUDIT_RUN_ID"
