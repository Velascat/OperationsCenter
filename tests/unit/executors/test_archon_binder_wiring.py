# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""R3 — Archon binder + adapter wiring tests."""
from __future__ import annotations


import pytest
import yaml

from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.executors.archon.binder import (
    BindError, bind, write_worktree_config,
)


class TestArchonBinder:
    def test_none_returns_default(self):
        sel = bind(None)
        assert sel.config_yaml is None
        assert sel.label == "archon_default"

    def test_cli_subscription_anthropic_opus_resolves(self):
        # Real Archon: provider literal is 'claude', model is bare 'opus'
        sel = bind(RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="opus",
        ))
        assert sel.config_yaml == {"provider": "claude", "model": "opus"}
        assert sel.label == "cli_subscription_opus"
        assert sel.provider == "claude"
        assert sel.model == "opus"

    def test_cli_subscription_sonnet_resolves(self):
        sel = bind(RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="sonnet",
        ))
        assert sel.model == "sonnet"
        assert sel.provider == "claude"

    def test_cli_subscription_non_anthropic_provider_raises(self):
        with pytest.raises(BindError, match="anthropic"):
            bind(RuntimeBindingSummary(
                kind="cli_subscription", selection_mode="explicit_request",
                provider="openai", model="gpt-4",
            ))

    def test_hosted_api_passes_model_through(self):
        # Real Archon: openai → 'codex' provider, model bare-as-given
        sel = bind(RuntimeBindingSummary(
            kind="hosted_api", selection_mode="explicit_request",
            provider="openai", model="gpt-4-turbo", endpoint="https://api.openai.com/v1",
        ))
        assert sel.config_yaml == {
            "provider": "codex", "model": "gpt-4-turbo",
            "base_url": "https://api.openai.com/v1",
        }
        assert sel.label == "hosted_codex_gpt-4-turbo"

    def test_hosted_api_requires_provider_and_model(self):
        with pytest.raises(BindError, match="provider"):
            bind(RuntimeBindingSummary(
                kind="hosted_api", selection_mode="explicit_request",
            ))


class TestWriteWorktreeConfig:
    def test_writes_config_yaml_into_worktree(self, tmp_path):
        sel = bind(RuntimeBindingSummary(
            kind="cli_subscription", selection_mode="explicit_request",
            provider="anthropic", model="opus",
        ))
        path = write_worktree_config(tmp_path, sel)
        assert path == tmp_path / ".archon" / "config.yaml"
        loaded = yaml.safe_load(path.read_text())
        assert loaded == {"provider": "claude", "model": "opus"}

    def test_returns_none_for_default_selection(self, tmp_path):
        path = write_worktree_config(tmp_path, bind(None))
        assert path is None
        assert not (tmp_path / ".archon").exists()


class TestArchonAdapterWiring:
    def test_adapter_writes_config_when_binding_present(self, tmp_path):
        from unittest.mock import MagicMock, patch
        from datetime import datetime, timezone

        from operations_center.backends.archon.adapter import ArchonBackendAdapter
        from operations_center.backends.archon.models import ArchonRunCapture
        from operations_center.contracts.execution import (
            ExecutionRequest, ExecutionResult, RuntimeBindingSummary,
        )
        from operations_center.contracts.enums import ExecutionStatus

        ws = tmp_path / "ws"
        ws.mkdir()

        request = ExecutionRequest(
            proposal_id="p", decision_id="d",
            goal_text="g", repo_key="r", clone_url="https://x",
            base_branch="main", task_branch="feat/x",
            workspace_path=ws,
            runtime_binding=RuntimeBindingSummary(
                kind="cli_subscription", selection_mode="explicit_request",
                provider="anthropic", model="opus",
            ),
        )

        adapter = ArchonBackendAdapter.__new__(ArchonBackendAdapter)
        adapter._invoker = MagicMock()
        now = datetime.now(timezone.utc)
        adapter._invoker.invoke.return_value = ArchonRunCapture(
            run_id="rc", outcome="success", exit_code=0,
            output_text="ok", error_text="",
            workflow_events=[], started_at=now, finished_at=now,
            duration_ms=100,
        )
        adapter._workflow_type = "goal"

        from operations_center.backends.archon.models import ArchonWorkflowConfig
        with patch("operations_center.backends.archon.adapter.check_support") as cs, \
             patch("operations_center.backends.archon.adapter.map_request") as mr, \
             patch("operations_center.backends.archon.adapter.normalize") as norm:
            cs.return_value = MagicMock(supported=True)
            # Use a real ArchonWorkflowConfig — adapter now applies dataclass.replace
            # to thread binder_provider/binder_model into the prepared config for
            # the HTTP-mode dispatch path (see PATCH-001).
            mr.return_value = ArchonWorkflowConfig(
                run_id="rc", goal_text="g", constraints_text=None,
                repo_path=ws, task_branch="feat/x", workflow_type="goal",
            )
            norm.return_value = ExecutionResult(
                run_id="r1", proposal_id="p", decision_id="d",
                status=ExecutionStatus.SUCCEEDED, success=True,
            )
            _, capture = adapter.execute_and_capture(request)

        # Config landed in the worktree
        cfg_path = ws / ".archon" / "config.yaml"
        assert cfg_path.exists()
        cfg = yaml.safe_load(cfg_path.read_text())
        assert cfg["provider"] == "claude"
        assert cfg["model"] == "opus"
        # Capture annotated for drift detection
        assert getattr(capture, "observed_runtime")["model"] == "opus"
        assert getattr(capture, "binder_label") == "cli_subscription_opus"
