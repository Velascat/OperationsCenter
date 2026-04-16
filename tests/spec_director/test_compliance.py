# tests/spec_director/test_compliance.py
from __future__ import annotations
from unittest.mock import patch
from control_plane.spec_director.models import ComplianceInput

_PATCH_TARGET = "control_plane.spec_director.compliance.call_claude"


def test_lgtm_verdict_parsed():
    from control_plane.spec_director.compliance import SpecComplianceService
    raw = '{"verdict": "LGTM", "spec_coverage": 0.95, "violations": [], "notes": "all good"}'
    with patch(_PATCH_TARGET, return_value=raw):
        service = SpecComplianceService(model="claude-sonnet-4-6")
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
    assert verdict.prompt_tokens == 0


def test_api_failure_returns_concerns():
    from control_plane.spec_director.compliance import SpecComplianceService
    with patch(_PATCH_TARGET, side_effect=Exception("network error")):
        service = SpecComplianceService(model="claude-sonnet-4-6", max_retries=1)
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
    large_diff = "+" + "x" * 5000

    captured = []
    def capture_call(user_prompt, **kwargs):
        captured.append(user_prompt)
        return raw

    with patch(_PATCH_TARGET, side_effect=capture_call):
        service = SpecComplianceService(model="claude-sonnet-4-6", max_diff_kb=1)
        inp = ComplianceInput(
            spec_text="# Spec", diff=large_diff, task_constraints="",
            task_phase="implement", spec_coverage_hint="Goal 1",
        )
        service.check(inp)
    assert "[diff truncated]" in captured[0]
