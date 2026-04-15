# tests/spec_director/test_compliance.py
from __future__ import annotations
from unittest.mock import MagicMock
from control_plane.spec_director.models import ComplianceInput


def _make_client(verdict_json: str):
    mock = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=verdict_json)]
    msg.usage.input_tokens = 200
    msg.usage.output_tokens = 100
    mock.messages.create.return_value = msg
    return mock


def test_lgtm_verdict_parsed():
    from control_plane.spec_director.compliance import SpecComplianceService
    raw = '{"verdict": "LGTM", "spec_coverage": 0.95, "violations": [], "notes": "all good"}'
    service = SpecComplianceService(client=_make_client(raw), model="claude-sonnet-4-6")
    inp = ComplianceInput(
        spec_text="# Spec\n## Goals\n1. Add auth",
        diff="diff --git a/src/auth/middleware.py\n+def authenticate(): pass",
        task_constraints="Only modify src/auth/",
        task_phase="implement",
        spec_coverage_hint="Goal 1",
    )
    verdict = service.check(inp)
    assert verdict.verdict == "LGTM"
    assert verdict.spec_coverage == 0.95
    assert verdict.prompt_tokens == 200


def test_api_failure_returns_concerns():
    from control_plane.spec_director.compliance import SpecComplianceService
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("network error")
    service = SpecComplianceService(client=mock_client, model="claude-sonnet-4-6", max_retries=1)
    inp = ComplianceInput(
        spec_text="# Spec", diff="", task_constraints="",
        task_phase="implement", spec_coverage_hint="Goal 1",
    )
    verdict = service.check(inp)
    assert verdict.verdict == "CONCERNS"
    assert "api_failure" in verdict.notes.lower() or "error" in verdict.notes.lower()


def test_truncates_large_diff():
    from control_plane.spec_director.compliance import SpecComplianceService
    raw = '{"verdict": "LGTM", "spec_coverage": 0.8, "violations": [], "notes": "ok"}'
    SpecComplianceService(client=_make_client(raw), model="claude-sonnet-4-6",
                                    max_diff_kb=1)
    large_diff = "+" + "x" * 5000

    captured = []
    mock_client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=raw)]
    msg.usage.input_tokens = 100
    msg.usage.output_tokens = 50
    def capture(**kwargs):
        captured.append(kwargs)
        return msg
    mock_client.messages.create.side_effect = capture
    service2 = SpecComplianceService(client=mock_client, model="claude-sonnet-4-6", max_diff_kb=1)
    inp = ComplianceInput(
        spec_text="# Spec", diff=large_diff, task_constraints="",
        task_phase="implement", spec_coverage_hint="Goal 1",
    )
    service2.check(inp)
    prompt_text = str(captured[0])
    assert "[diff truncated]" in prompt_text
