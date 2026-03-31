from __future__ import annotations

import re
from typing import Any

import httpx
import yaml

from control_plane.domain import BoardTask

EXEC_BLOCK_PATTERN = re.compile(r"## Execution\n(.*?)(?:\n## |\Z)", re.DOTALL)


class PlaneClient:
    def __init__(self, base_url: str, api_token: str, workspace_slug: str, project_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_slug = workspace_slug
        self.project_id = project_id
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"x-api-key": api_token, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def fetch_issue(self, task_id: str) -> dict[str, Any]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/issues/{task_id}/"
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def to_board_task(self, issue: dict[str, Any]) -> BoardTask:
        metadata = self.parse_execution_metadata(issue.get("description_html") or issue.get("description_stripped") or "")
        return BoardTask(
            task_id=str(issue["id"]),
            project_id=str(issue.get("project_id", self.project_id)),
            title=issue.get("name", "Untitled"),
            description=issue.get("description_stripped"),
            status=issue.get("state", {}).get("name", "Unknown"),
            labels=[label.get("name", "") for label in issue.get("labels", []) if isinstance(label, dict)],
            repo_key=metadata["repo"],
            base_branch=metadata["base_branch"],
            execution_mode=metadata["mode"],
            allowed_paths=metadata.get("allowed_paths", []),
            validation_profile=metadata.get("validation_profile"),
            open_pr=bool(metadata.get("open_pr", False)),
        )

    def parse_execution_metadata(self, description: str) -> dict[str, Any]:
        match = EXEC_BLOCK_PATTERN.search(description)
        if not match:
            raise ValueError("Missing '## Execution' block in task description")

        yaml_block = match.group(1).strip()
        data = yaml.safe_load(yaml_block) or {}
        required = ("repo", "base_branch", "mode")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"Missing execution metadata fields: {', '.join(missing)}")
        return data

    def transition_issue(self, task_id: str, state_name: str) -> None:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/issues/{task_id}/"
        response = self._client.patch(url, json={"state": state_name})
        response.raise_for_status()

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        url = (
            f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/"
            f"issues/{task_id}/comments/"
        )
        response = self._client.post(url, json={"comment_html": comment_markdown})
        response.raise_for_status()
