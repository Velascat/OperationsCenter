from __future__ import annotations

import re
from typing import Any

import yaml

from control_plane.domain import ParsedTaskBody

SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class TaskParser:
    REQUIRED_EXEC_FIELDS = ("repo", "base_branch", "mode")

    def parse(self, description: str) -> ParsedTaskBody:
        sections = self._extract_sections(description)
        execution_raw = sections.get("execution", "").strip()
        if not execution_raw:
            raise ValueError("Missing '## Execution' section in task description")

        metadata = yaml.safe_load(execution_raw) or {}
        if not isinstance(metadata, dict):
            raise ValueError("Execution section must be valid key/value YAML")

        missing = [key for key in self.REQUIRED_EXEC_FIELDS if key not in metadata]
        if missing:
            raise ValueError(f"Missing execution metadata fields: {', '.join(missing)}")

        goal_text = sections.get("goal", "").strip()
        if not goal_text:
            raise ValueError("Missing '## Goal' section in task description")

        constraints_text = sections.get("constraints", "").strip() or None
        return ParsedTaskBody(
            execution_metadata=self._normalize_metadata(metadata),
            goal_text=goal_text,
            constraints_text=constraints_text,
        )

    def _extract_sections(self, description: str) -> dict[str, str]:
        headers = list(SECTION_PATTERN.finditer(description))
        if not headers:
            return {}

        sections: dict[str, str] = {}
        for idx, match in enumerate(headers):
            title = match.group(1).strip().lower()
            content_start = match.end()
            content_end = headers[idx + 1].start() if idx + 1 < len(headers) else len(description)
            sections[title] = description[content_start:content_end].strip("\n")
        return sections

    @staticmethod
    def _normalize_metadata(metadata: dict[str, Any]) -> dict[str, object]:
        data = dict(metadata)
        allowed_paths = data.get("allowed_paths", [])
        if isinstance(allowed_paths, str):
            allowed_paths = [allowed_paths]
        data["allowed_paths"] = [str(path).strip() for path in allowed_paths if str(path).strip()]
        data["open_pr"] = bool(data.get("open_pr", False))
        return data
