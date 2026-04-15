# src/control_plane/spec_director/brainstorm.py
from __future__ import annotations

import uuid
from dataclasses import dataclass

from control_plane.spec_director.context_bundle import ContextBundle
from control_plane.spec_director.models import SpecFrontMatter

_SYSTEM_PROMPT = """\
You are a software architect for a Python repository. Given signals about the codebase's \
current state, generate a focused spec document for the next improvement campaign.

The spec MUST begin with a YAML front matter block in this exact format:
---
campaign_id: <a UUID v4 you generate>
slug: <kebab-case-slug>
phases:
  - implement
  - test
  - improve
repos:
  - <repo name from context>
area_keywords:
  - <directory prefix or topic keyword>
status: active
created_at: <ISO 8601 timestamp>
---

After the front matter, write a markdown spec document with:
- ## Overview (2-3 sentences)
- ## Goals (numbered list of concrete, bounded tasks)
- ## Constraints (approach decisions, allowed paths, things to avoid)
- ## Success Criteria (how to know it is done)

Be specific and bounded. Prefer 2-4 goals over vague large ones. \
Each goal should be completable by one kodo run in under 1 hour."""

_SYSTEM_PROMPT_CACHED = True


class BrainstormError(Exception):
    pass


@dataclass
class BrainstormResult:
    spec_text: str
    slug: str
    phases: list[str]
    area_keywords: list[str]
    campaign_id: str
    prompt_tokens: int
    completion_tokens: int


class BrainstormService:
    def __init__(self, client: object, model: str = "claude-opus-4-6") -> None:
        self._client = client
        self._model = model

    def brainstorm(self, bundle: ContextBundle) -> BrainstormResult:
        user_content = self._build_user_prompt(bundle)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_content}],
            )
        except Exception as exc:
            raise BrainstormError(f"Anthropic API call failed: {exc}") from exc

        raw = response.content[0].text
        try:
            fm = SpecFrontMatter.from_spec_text(raw)
        except Exception as exc:
            raise BrainstormError(f"Response missing valid YAML front matter: {exc}") from exc

        return BrainstormResult(
            spec_text=raw,
            slug=fm.slug,
            phases=fm.phases,
            area_keywords=fm.area_keywords,
            campaign_id=fm.campaign_id or str(uuid.uuid4()),
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )

    @staticmethod
    def _build_user_prompt(bundle: ContextBundle) -> str:
        parts = []
        if bundle.seed_text:
            parts.append(f"## Operator Direction\n{bundle.seed_text}")
        if bundle.insight_snapshot:
            parts.append(f"## Insight Snapshot\n```json\n{bundle.insight_snapshot}\n```")
        if bundle.git_log:
            parts.append(f"## Recent Git Activity\n```\n{bundle.git_log}\n```")
        if bundle.specs_index:
            lines = "\n".join(f"- {s.get('slug', '?')} ({s.get('status', '?')})" for s in bundle.specs_index)
            parts.append(f"## Existing Specs\n{lines}")
        if bundle.board_summary:
            lines = "\n".join(f"- {t.get('name', t.get('title', '?'))} [{t.get('state', '?')}]" for t in bundle.board_summary[:50])
            parts.append(f"## Current Board\n{lines}")
        parts.append("Generate the spec document now.")
        return "\n\n".join(parts)

    @staticmethod
    def make_client(api_key_env: str = "ANTHROPIC_API_KEY") -> object:
        import os
        import anthropic
        return anthropic.Anthropic(api_key=os.environ[api_key_env])
