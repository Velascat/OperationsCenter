# src/control_plane/spec_director/brainstorm.py
from __future__ import annotations

import uuid
from dataclasses import dataclass

from control_plane.spec_director._claude_cli import call_claude
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
  - <one of the available repos listed in the context>
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
Each goal should be completable by one kodo run in under 1 hour. \
Set repos: to exactly one repo from the available repos list."""


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
    def __init__(self, model: str = "claude-opus-4-6") -> None:
        self._model = model

    def brainstorm(self, bundle: ContextBundle) -> BrainstormResult:
        user_content = self._build_user_prompt(bundle)
        try:
            raw = call_claude(user_content, system_prompt=_SYSTEM_PROMPT, model=self._model)
        except Exception as exc:
            raise BrainstormError(f"claude CLI call failed: {exc}") from exc

        # The CLI may wrap the spec in a markdown code fence (```markdown ... ```)
        # or add conversational preamble. Strip both so the parser sees raw spec text.
        import re as _re
        # Remove ```markdown / ```yaml / ``` fences and their closing ```
        raw = _re.sub(r"^```(?:markdown|yaml)?\n", "", raw, flags=_re.MULTILINE)
        raw = _re.sub(r"\n```\s*(?:\n|$)", "\n", raw, flags=_re.MULTILINE)
        raw = raw.strip()
        # Find the --- that is immediately followed by a YAML key (word: value),
        # skipping bare --- separators that appear before the real front matter.
        m = _re.search(r"(---\n\w+:)", raw)
        if m:
            raw = raw[m.start():]

        try:
            fm = SpecFrontMatter.from_spec_text(raw)
        except Exception as exc:
            import logging as _logging
            _logging.getLogger(__name__).error(
                "brainstorm_parse_failure | first_300: %r", raw[:300]
            )
            raise BrainstormError(f"Response missing valid YAML front matter: {exc}") from exc

        return BrainstormResult(
            spec_text=raw,
            slug=fm.slug,
            phases=fm.phases,
            area_keywords=fm.area_keywords,
            campaign_id=fm.campaign_id or str(uuid.uuid4()),
            prompt_tokens=0,
            completion_tokens=0,
        )

    @staticmethod
    def _build_user_prompt(bundle: ContextBundle) -> str:
        parts = []
        if bundle.available_repos:
            parts.append("## Available Repos\n" + "\n".join(f"- {r}" for r in bundle.available_repos))
        if bundle.seed_text:
            parts.append(f"## Operator Direction\n{bundle.seed_text}")
        for repo_key, log_text in bundle.git_logs.items():
            if log_text:
                parts.append(f"## Recent Git Activity ({repo_key})\n```\n{log_text}\n```")
        if bundle.specs_index:
            lines = "\n".join(f"- {s.get('slug', '?')} ({s.get('status', '?')})" for s in bundle.specs_index)
            parts.append(f"## Existing Specs\n{lines}")
        if bundle.recent_done_tasks:
            lines = "\n".join(f"- {t.get('name', '?')} [Done]" for t in bundle.recent_done_tasks[:20])
            parts.append(f"## Recently Completed Tasks\n{lines}")
        if bundle.recent_cancelled_tasks:
            lines = "\n".join(f"- {t.get('name', '?')} [Cancelled]" for t in bundle.recent_cancelled_tasks[:10])
            parts.append(f"## Recently Cancelled Tasks (avoid re-proposing)\n{lines}")
        parts.append(f"## Board Summary\n{bundle.open_task_count} open task(s) currently active.")
        parts.append("Generate the spec document now.")
        return "\n\n".join(parts)
