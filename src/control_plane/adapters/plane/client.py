from __future__ import annotations

import html
import re
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
        response = self._client.get(url, params={"expand": "state"})
        response.raise_for_status()
        return response.json()

    def fetch_project(self) -> dict[str, Any]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/"
        response = self._client.get(url)
        response.raise_for_status()
        return response.json()

    def list_issues(self) -> list[dict[str, Any]]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/"
        response = self._client.get(url, params={"expand": "state"})
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        return []

    def list_states(self) -> list[dict[str, Any]]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/states/"
        response = self._client.get(url)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, dict)]
        return []

    def to_board_task(self, issue: dict[str, Any]) -> BoardTask:
        description = self._issue_description_text(issue)
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
            execution_mode=metadata["mode"],
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
        state_value: str = state
        for item in self.list_states():
            if str(item.get("name", "")).strip().lower() == state.strip().lower():
                state_value = str(item["id"])
                break
        response = self._client.patch(url, json={"state": state_value})
        response.raise_for_status()

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        url = (
            f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/"
            f"work-items/{task_id}/comments/"
        )
        response = self._client.post(url, json={"comment_html": self._render_comment_html(comment_markdown)})
        response.raise_for_status()

    @staticmethod
    def _render_comment_html(comment_markdown: str) -> str:
        lines = [line.strip() for line in comment_markdown.splitlines() if line.strip()]
        if not lines:
            return "<p>(no summary)</p>"

        header = html.escape(lines[0])
        items: list[str] = []
        for line in lines[1:]:
            if line.startswith("- "):
                items.append(f"<li>{html.escape(line[2:])}</li>")

        if items:
            return f"<p>{header}</p><ul>{''.join(items)}</ul>"
        return f"<p>{header}</p>"

    @staticmethod
    def _issue_description_text(issue: dict[str, Any]) -> str:
        raw = issue.get("description") or issue.get("description_stripped")
        if isinstance(raw, str) and raw.strip():
            return raw
        html_body = issue.get("description_html")
        if isinstance(html_body, str) and html_body.strip():
            return PlaneClient._html_to_task_text(html_body)
        return ""

    @staticmethod
    def _html_to_task_text(html_body: str) -> str:
        text = html.unescape(html_body)
        text = re.sub(r"<h[1-6][^>]*>\s*(.*?)\s*</h[1-6]>", lambda m: f"\n## {m.group(1)}\n", text, flags=re.I | re.S)
        text = re.sub(r"<li[^>]*>\s*(.*?)\s*</li>", lambda m: f"- {m.group(1)}\n", text, flags=re.I | re.S)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</?(p|div|ul|ol|pre)[^>]*>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
