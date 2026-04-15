# src/control_plane/spec_director/compliance.py
from __future__ import annotations

import json
import logging

from control_plane.spec_director.models import ComplianceInput, ComplianceVerdict

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a code reviewer checking whether a git diff implements what was specified in a spec document.

Respond with ONLY valid JSON matching this schema:
{
  "verdict": "LGTM" | "CONCERNS" | "FAIL",
  "spec_coverage": <float 0.0-1.0>,
  "violations": [<string>, ...],
  "notes": "<short summary>"
}

Verdict meanings:
- LGTM: diff implements the spec section, no violations
- CONCERNS: diff partially implements or has minor issues — human should review
- FAIL: diff clearly contradicts the spec or violates stated constraints"""


class SpecComplianceService:
    def __init__(
        self,
        client: object,
        model: str = "claude-sonnet-4-6",
        max_retries: int = 2,
        max_diff_kb: int = 32,
    ) -> None:
        self._client = client
        self._model = model
        self._max_retries = max_retries
        self._max_diff_bytes = max_diff_kb * 1024

    def check(self, inp: ComplianceInput) -> ComplianceVerdict:
        diff = inp.diff
        truncated = False
        if len(diff.encode()) > self._max_diff_bytes:
            diff = diff.encode()[: self._max_diff_bytes].decode("utf-8", errors="replace")
            truncated = True

        user_prompt = self._build_prompt(inp, diff, truncated)
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=1024,
                    system=[
                        {
                            "type": "text",
                            "text": _SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_prompt}],
                )
                raw = response.content[0].text.strip()
                data = json.loads(raw)
                return ComplianceVerdict(
                    verdict=data["verdict"],
                    spec_coverage=float(data.get("spec_coverage", 0.5)),
                    violations=list(data.get("violations", [])),
                    notes=str(data.get("notes", "")),
                    model=self._model,
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                )
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    '{"event": "compliance_check_error", "attempt": %d, "error": "%s"}',
                    attempt + 1, str(exc),
                )
        return ComplianceVerdict(
            verdict="CONCERNS",
            spec_coverage=0.0,
            violations=[],
            notes=f"api_failure after {self._max_retries} attempts: {last_exc}",
            model=self._model,
            prompt_tokens=0,
            completion_tokens=0,
        )

    @staticmethod
    def _build_prompt(inp: ComplianceInput, diff: str, truncated: bool) -> str:
        trunc_note = "\n[diff truncated]" if truncated else ""
        return f"""## Spec Document
{inp.spec_text}

## Task Phase
{inp.task_phase}

## Spec Section Addressed
{inp.spec_coverage_hint}

## Task Constraints
{inp.task_constraints}

## Git Diff
```diff
{diff}{trunc_note}
```

Review the diff against the spec and respond with JSON only."""
