# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Production ExecutionRequestBuilder reads runtime_binding from
ExecutionRuntimeContext (not from LaneDecision). SwitchBoard stays
untouched — OC's policy/binder layer attaches the binding.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))
import test_coordinator as tc  # noqa: E402

from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.execution.handoff import (
    ExecutionRequestBuilder,
    ExecutionRuntimeContext,
)


def test_runtime_context_default_no_binding():
    ctx = ExecutionRuntimeContext(workspace_path=Path("/tmp/ws"), task_branch="t")
    assert ctx.runtime_binding is None


def test_runtime_context_carries_binding():
    binding = RuntimeBindingSummary(
        kind="cli_subscription", selection_mode="explicit_request",
        provider="anthropic", model="opus",
    )
    ctx = ExecutionRuntimeContext(
        workspace_path=Path("/tmp/ws"), task_branch="t",
        runtime_binding=binding,
    )
    assert ctx.runtime_binding.model == "opus"


def test_production_builder_propagates_binding_to_request():
    bundle = tc._bundle()
    binding = RuntimeBindingSummary(
        kind="cli_subscription", selection_mode="explicit_request",
        provider="anthropic", model="opus",
    )
    ctx = ExecutionRuntimeContext(
        workspace_path=Path("/tmp/ws"), task_branch="t",
        runtime_binding=binding,
    )
    req = ExecutionRequestBuilder().build(bundle, ctx)
    assert req.runtime_binding is not None
    assert req.runtime_binding.model == "opus"
    assert req.runtime_binding.kind == "cli_subscription"


def test_production_builder_no_binding_when_context_omits_it():
    bundle = tc._bundle()
    ctx = ExecutionRuntimeContext(workspace_path=Path("/tmp/ws"), task_branch="t")
    req = ExecutionRequestBuilder().build(bundle, ctx)
    assert req.runtime_binding is None
