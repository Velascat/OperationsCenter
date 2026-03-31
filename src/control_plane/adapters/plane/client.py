from __future__ import annotations

import html
from typing import Any

import httpx

from control_plane.application.task_parser import TaskParser
from control_plane.domain import BoardTask


class PlaneClient:
    def __init__(self, base_url: str, api_token: str, workspace_slug: str, project_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_slug = workspace_slug
        self.project_id = project_id
        self.task_parser = TaskParser()
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": api_token, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def fetch_issue(self, task_id: str) -> dict[str, Any]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{task_id}/"
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def to_board_task(self, issue: dict[str, Any]) -> BoardTask:
        description = issue.get("description") or issue.get("description_stripped") or ""
        parsed_body = self.task_parser.parse(description)
        metadata = parsed_body.execution_metadata
        state = issue.get("state")
        status_value = state.get("name", "Unknown") if isinstance(state, dict) else str(state or "Unknown")
        return BoardTask(
            task_id=str(issue["id"]),
            project_id=str(issue.get("project_id", self.project_id)),
            title=issue.get("name", "Untitled"),
            description=description,
            status=status_value,
            labels=[label.get("name", "") for label in issue.get("labels", []) if isinstance(label, dict)],
            repo_key=str(metadata["repo"]),
            base_branch=str(metadata["base_branch"]),
            execution_mode=str(metadata["mode"]),
            allowed_paths=[str(path) for path in metadata.get("allowed_paths", [])],
            validation_profile=(
                str(metadata.get("validation_profile")) if metadata.get("validation_profile") else None
            ),
            open_pr=bool(metadata.get("open_pr", False)),
            goal_text=parsed_body.goal_text,
            constraints_text=parsed_body.constraints_text,
        )

    def transition_issue(self, task_id: str, state: str) -> None:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{task_id}/"
        response = self._client.patch(url, json={"state": state})
        response.raise_for_status()

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        url = (
            f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/"
            f"work-items/{task_id}/comments/"
        )
        html_body = "<p>" + "</p><p>".join(
            html.escape(line) for line in comment_markdown.split("\n") if line.strip()
        ) + "</p>"
        response = self._client.post(url, json={"comment_html": html_body})
        response.raise_for_status()
