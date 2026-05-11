# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""R1 — RuntimeBinding wired through OC's ExecutionRequest + CxRP mapper."""
from __future__ import annotations

from pathlib import Path

import pytest
from cxrp.contracts import RuntimeBinding as CxrpRuntimeBinding
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.contracts.cxrp_mapper import (
    runtime_binding_from_summary,
    runtime_binding_to_summary,
    to_cxrp_execution_request,
)
from operations_center.contracts.execution import (
    ExecutionRequest,
    RuntimeBindingSummary,
)


def _request(**overrides) -> ExecutionRequest:
    base = dict(
        proposal_id="p", decision_id="d",
        goal_text="design auth subsystem",
        repo_key="r", clone_url="https://x", base_branch="main",
        task_branch="feat/x",
        workspace_path=Path("/tmp/ws"),
    )
    base.update(overrides)
    return ExecutionRequest(**base)


class TestRequestCarriesBinding:
    def test_default_request_has_no_binding(self):
        req = _request()
        assert req.runtime_binding is None

    def test_request_with_binding_round_trips(self):
        binding = RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="opus",
        )
        req = _request(runtime_binding=binding)
        assert req.runtime_binding.kind == "cli_subscription"
        assert req.runtime_binding.model == "opus"
        # frozen
        with pytest.raises(Exception):
            req.runtime_binding = None  # type: ignore[misc]


class TestSummaryCxrpRoundTrip:
    def test_summary_to_cxrp_validates(self):
        s = RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="opus",
        )
        rb = runtime_binding_from_summary(s)
        assert rb.kind == RuntimeKind.CLI_SUBSCRIPTION
        assert rb.selection_mode == SelectionMode.EXPLICIT_REQUEST
        assert rb.model == "opus"

    def test_summary_to_cxrp_rejects_inconsistent_shape(self):
        # human + model is forbidden; the canonical CxRP RuntimeBinding now
        # rejects it at construction time instead of letting an OC mirror
        # carry the invalid shape further downstream.
        with pytest.raises(ValueError, match="model"):
            RuntimeBindingSummary(
                kind="human", selection_mode="explicit_request", model="opus",
            )

    def test_cxrp_to_summary_preserves_fields(self):
        rb = CxrpRuntimeBinding(
            kind=RuntimeKind.HOSTED_API,
            selection_mode=SelectionMode.POLICY_SELECTED,
            provider="anthropic", model="sonnet", endpoint="https://api.anthropic.com",
        )
        s = runtime_binding_to_summary(rb)
        assert s.kind == "hosted_api"
        assert s.selection_mode == "policy_selected"
        assert s.endpoint == "https://api.anthropic.com"


class TestExecutionRequestMapper:
    def test_oc_request_with_binding_emits_cxrp_with_binding(self):
        binding = RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="opus",
        )
        oc = _request(runtime_binding=binding)
        cxrp_req = to_cxrp_execution_request(oc, executor="claude_cli", backend="kodo")
        assert cxrp_req.runtime_binding is not None
        assert cxrp_req.runtime_binding.kind.value == "cli_subscription"
        assert cxrp_req.runtime_binding.model == "opus"

    def test_oc_request_without_binding_emits_cxrp_without_binding(self):
        oc = _request()
        cxrp_req = to_cxrp_execution_request(oc, executor="claude_cli", backend="kodo")
        assert cxrp_req.runtime_binding is None
