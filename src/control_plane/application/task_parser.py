from __future__ import annotations

import re
from typing import Any

import yaml

from control_plane.domain import ParsedTaskBody

SECTION_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class TaskParser:
    REQUIRED_EXEC_FIELDS = ("repo",)
    SUPPORTED_MODES = {"goal", "fix_pr", "test_campaign", "improve_campaign"}
    _CAMPAIGN_PASSTHROUGH_FIELDS = {"spec_campaign_id", "spec_file", "task_phase", "spec_coverage_hint"}

    def parse(self, description: str, *, labels: list[str] | None = None) -> ParsedTaskBody:
        label_repo = self._repo_from_labels(labels or [])

        sections = self._extract_sections(description or "")
        execution_raw = sections.get("execution", "").strip()

        if execution_raw:
            metadata = yaml.safe_load(execution_raw) or {}
            if not isinstance(metadata, dict):
                raise ValueError("Execution section must be valid key/value YAML")
            if "repo" not in metadata and label_repo:
                metadata["repo"] = label_repo
        elif label_repo:
            metadata = {"repo": label_repo}
        else:
            raise ValueError(
                "Missing '## Execution' section and no 'repo: <key>' label found on the task"
            )

        if "mode" not in metadata:
            metadata["mode"] = "goal"

        missing = [key for key in self.REQUIRED_EXEC_FIELDS if key not in metadata]
        if missing:
            raise ValueError(f"Missing execution metadata fields: {', '.join(missing)}")

        # base_branch is optional — resolved from repo config when absent
        if "base_branch" not in metadata:
            metadata["base_branch"] = ""

        goal_text = sections.get("goal", "").strip()
        if not goal_text:
            # No ## Goal section — treat full description as the goal (label-based tasks)
            goal_text = (description or "").strip()
        if not goal_text:
            raise ValueError(
                "Missing goal text. Add a '## Goal' section or write the goal as the task description."
            )

        constraints_text = sections.get("constraints", "").strip() or None
        return ParsedTaskBody(
            execution_metadata=self._normalize_metadata(metadata),
            goal_text=goal_text,
            constraints_text=constraints_text,
        )

    @staticmethod
    def _repo_from_labels(labels: list[str]) -> str | None:
        for label in labels:
            if label.lower().startswith("repo:"):
                value = label.split(":", 1)[1].strip()
                if value:
                    return value
        return None

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

    def _normalize_metadata(self, metadata: dict[str, Any]) -> dict[str, object]:
        data = dict(metadata)
        mode = str(data["mode"]).strip().lower()
        if mode not in self.SUPPORTED_MODES:
            raise ValueError(
                f"Unsupported execution mode '{data['mode']}'. Supported: {sorted(self.SUPPORTED_MODES)}"
            )
        data["mode"] = mode

        allowed_paths = data.get("allowed_paths", [])
        if isinstance(allowed_paths, str):
            allowed_paths = [allowed_paths]
        data["allowed_paths"] = [str(path).strip() for path in allowed_paths if str(path).strip()]
        data["open_pr"] = bool(data.get("open_pr", False))
        # Pass campaign metadata fields through unchanged
        for field in self._CAMPAIGN_PASSTHROUGH_FIELDS:
            if field in data:
                data[field] = str(data[field]).strip()
        return data
