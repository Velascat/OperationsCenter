# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Tests for ManagedAuditInvocationRequest contract model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from operations_center.audit_toolset.contracts import ManagedAuditInvocationRequest

_RUN_ID = "3dead998d4c44e1cb296bef061de50f3"

_MINIMAL = {
    "repo_id": "videofoundry",
    "audit_type": "representative",
    "run_id": _RUN_ID,
    "working_directory": ".",
    "command": "python -m tools.audit.cli.run_representative_audit",
    "env": {"AUDIT_RUN_ID": _RUN_ID},
    "expected_output_dir": "tools/audit/report/representative",
}


class TestManagedAuditInvocationRequest:
    def test_parses_minimal(self) -> None:
        req = ManagedAuditInvocationRequest.model_validate(_MINIMAL)
        assert req.repo_id == "videofoundry"
        assert req.audit_type == "representative"
        assert req.run_id == _RUN_ID

    def test_requires_repo_id(self) -> None:
        data = {k: v for k, v in _MINIMAL.items() if k != "repo_id"}
        with pytest.raises(ValidationError):
            ManagedAuditInvocationRequest.model_validate(data)

    def test_requires_audit_type(self) -> None:
        data = {k: v for k, v in _MINIMAL.items() if k != "audit_type"}
        with pytest.raises(ValidationError):
            ManagedAuditInvocationRequest.model_validate(data)

    def test_requires_run_id(self) -> None:
        data = {k: v for k, v in _MINIMAL.items() if k != "run_id"}
        with pytest.raises(ValidationError):
            ManagedAuditInvocationRequest.model_validate(data)

    def test_audit_run_id_must_be_in_env(self) -> None:
        data = {**_MINIMAL, "env": {}}
        with pytest.raises(ValidationError, match="AUDIT_RUN_ID"):
            ManagedAuditInvocationRequest.model_validate(data)

    def test_audit_run_id_must_match_run_id(self) -> None:
        data = {**_MINIMAL, "env": {"AUDIT_RUN_ID": "different_value"}}
        with pytest.raises(ValidationError, match="AUDIT_RUN_ID"):
            ManagedAuditInvocationRequest.model_validate(data)

    def test_extra_env_preserved(self) -> None:
        data = {**_MINIMAL, "env": {"AUDIT_RUN_ID": _RUN_ID, "MY_VAR": "hello"}}
        req = ManagedAuditInvocationRequest.model_validate(data)
        assert req.env["MY_VAR"] == "hello"

    def test_metadata_defaults_empty(self) -> None:
        req = ManagedAuditInvocationRequest.model_validate(_MINIMAL)
        assert req.metadata == {}

    def test_metadata_accepted(self) -> None:
        data = {**_MINIMAL, "metadata": {"channel_slug": "Connective_Contours"}}
        req = ManagedAuditInvocationRequest.model_validate(data)
        assert req.metadata["channel_slug"] == "Connective_Contours"

    def test_command_is_string(self) -> None:
        req = ManagedAuditInvocationRequest.model_validate(_MINIMAL)
        assert isinstance(req.command, str)

    def test_expected_output_dir_is_string(self) -> None:
        req = ManagedAuditInvocationRequest.model_validate(_MINIMAL)
        assert isinstance(req.expected_output_dir, str)
