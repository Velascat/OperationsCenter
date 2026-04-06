from __future__ import annotations

import html
import re
import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from control_plane.application.task_parser import TaskParser
from control_plane.domain import BoardTask


class PlaneClient:
    def __init__(self, base_url: str, api_token: str, workspace_slug: str, project_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_slug = workspace_slug
        self.project_id = project_id
        self.task_parser = TaskParser()
        self._states_cache: list[dict[str, Any]] | None = None
        self._labels_cache: list[dict[str, Any]] | None = None
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": api_token, "Content-Type": "application/json"},
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def fetch_issue(self, task_id: str) -> dict[str, Any]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{task_id}/"
        response = self._request("GET", url, params={"expand": "state"})
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return self._hydrate_issue_labels(payload)
        return payload

    def fetch_project(self) -> dict[str, Any]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/"
        response = self._request("GET", url)
        response.raise_for_status()
        return response.json()

    def list_issues(self) -> list[dict[str, Any]]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/"
        response = self._request("GET", url, params={"expand": "state"})
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return [self._hydrate_issue_labels(item) for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                return [self._hydrate_issue_labels(item) for item in results if isinstance(item, dict)]
        return []

    def list_states(self) -> list[dict[str, Any]]:
        if self._states_cache is not None:
            return list(self._states_cache)
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/states/"
        response = self._request("GET", url)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            self._states_cache = [item for item in payload if isinstance(item, dict)]
            return list(self._states_cache)
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                self._states_cache = [item for item in results if isinstance(item, dict)]
                return list(self._states_cache)
        return []

    def list_labels(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        if self._labels_cache is not None and not force_refresh:
            return list(self._labels_cache)
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/labels/"
        response = self._request("GET", url)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            self._labels_cache = [item for item in payload if isinstance(item, dict)]
            return list(self._labels_cache)
        if isinstance(payload, dict):
            results = payload.get("results")
            if isinstance(results, list):
                self._labels_cache = [item for item in results if isinstance(item, dict)]
                return list(self._labels_cache)
        return []

    def list_comments(self, task_id: str) -> list[dict[str, Any]]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{task_id}/comments/"
        response = self._request("GET", url)
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
        label_names = [label.get("name", "") for label in issue.get("labels", []) if isinstance(label, dict)]
        parsed_body = self.task_parser.parse(description, labels=label_names)
        metadata = parsed_body.execution_metadata
        state = issue.get("state")
        status_value = state.get("name", "Unknown") if isinstance(state, dict) else str(state or "Unknown")
        return BoardTask(
            task_id=str(issue["id"]),
            project_id=str(issue.get("project_id", self.project_id)),
            title=issue.get("name", "Untitled"),
            description=description,
            status=status_value,
            labels=label_names,
            repo_key=str(metadata["repo"]),
            base_branch=str(metadata["base_branch"]),
            execution_mode=cast("Any", metadata.get("mode", "goal")),
            allowed_paths=[str(path) for path in cast(list[object], metadata.get("allowed_paths") or [])],
            validation_profile=(
                str(metadata.get("validation_profile")) if metadata.get("validation_profile") else None
            ),
            open_pr=bool(metadata.get("open_pr", False)),
            goal_text=parsed_body.goal_text,
            constraints_text=parsed_body.constraints_text,
        )

    def transition_issue(self, task_id: str, state: str) -> None:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{task_id}/"
        state_value = self._resolve_state_value(state)
        today = datetime.now(UTC).date().isoformat()
        payload: dict[str, Any] = {"state": state_value}
        if state == "Running":
            payload["start_date"] = today
        elif state in ("Done", "Review", "In Review", "Blocked"):
            payload["target_date"] = today
        response = self._request("PATCH", url, json=payload)
        response.raise_for_status()

    def create_issue(
        self,
        *,
        name: str,
        description: str,
        state: str | None = None,
        label_names: list[str] | None = None,
    ) -> dict[str, Any]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/"
        payload: dict[str, Any] = {
            "name": name,
            "description_stripped": description,
            "description_html": self._render_text_html(description),
        }
        if state:
            payload["state"] = self._resolve_state_value(state)
        if label_names:
            payload["labels"] = self._ensure_label_ids(label_names)
        response = self._request("POST", url, json=payload)
        response.raise_for_status()
        return response.json()

    def comment_issue(self, task_id: str, comment_markdown: str) -> None:
        url = (
            f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/"
            f"work-items/{task_id}/comments/"
        )
        response = self._request("POST", url, json={"comment_html": self._render_comment_html(comment_markdown)})
        response.raise_for_status()

    def _resolve_state_value(self, state: str) -> str:
        state_value: str = state
        for item in self.list_states():
            if str(item.get("name", "")).strip().lower() == state.strip().lower():
                state_value = str(item["id"])
                break
        return state_value

    def _ensure_label_ids(self, label_names: list[str]) -> list[str]:
        existing = {
            str(item.get("name", "")).strip().lower(): str(item["id"])
            for item in self.list_labels()
            if item.get("id") and item.get("name")
        }
        ids: list[str] = []
        for label_name in label_names:
            normalized = label_name.strip().lower()
            if not normalized:
                continue
            label_id = existing.get(normalized)
            if label_id is None:
                created = self._create_label(label_name.strip())
                label_id = str(created["id"])
                existing[normalized] = label_id
            ids.append(label_id)
        return ids

    def _create_label(self, label_name: str) -> dict[str, Any]:
        url = f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/labels/"
        response = self._request("POST", url, json={"name": label_name})
        response.raise_for_status()
        created = response.json()
        if self._labels_cache is not None and isinstance(created, dict):
            self._labels_cache.append(created)
        return created

    def _hydrate_issue_labels(self, issue: dict[str, Any]) -> dict[str, Any]:
        raw_labels = issue.get("labels")
        if not isinstance(raw_labels, list) or not raw_labels:
            return issue
        if all(isinstance(label, dict) for label in raw_labels):
            return issue

        def label_map(*, force_refresh: bool = False) -> dict[str, Any]:
            return {
                str(label.get("id")): label
                for label in self.list_labels(force_refresh=force_refresh)
                if isinstance(label, dict) and label.get("id")
            }

        by_id = label_map()
        unresolved = [str(raw) for raw in raw_labels if not isinstance(raw, dict) and str(raw) not in by_id]
        if unresolved:
            by_id = label_map(force_refresh=True)
        hydrated: list[Any] = []
        for raw in raw_labels:
            if isinstance(raw, dict):
                hydrated.append(raw)
            else:
                mapped = by_id.get(str(raw))
                hydrated.append(mapped or raw)
        issue["labels"] = hydrated
        return issue

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Execute an HTTP request with retry on 429, transient 5xx, and connection errors.

        Retries up to 3 times (4 total attempts).  Connection-level failures
        (ConnectError, TimeoutException) and 502/503/504 responses are retried
        with linear backoff.  429 responses honour the Retry-After header.
        Duplicate side-effects (e.g. duplicate comments from a retried POST) are
        acceptable — a missed board transition is far more damaging than a
        duplicate comment.
        """
        attempts = 4
        for attempt in range(1, attempts + 1):
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError):
                if attempt == attempts:
                    raise
                time.sleep(attempt * 2)
                continue
            if response.status_code == 429:
                if attempt == attempts:
                    return response
                retry_after_header = response.headers.get("Retry-After", "").strip()
                retry_after = int(retry_after_header) if retry_after_header.isdigit() else attempt * 2
                time.sleep(retry_after)
                continue
            # Retry on transient gateway / server errors regardless of HTTP method.
            if response.status_code in (502, 503, 504) and attempt < attempts:
                time.sleep(attempt * 2)
                continue
            return response
        raise RuntimeError("unreachable")

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
    def _render_text_html(text: str) -> str:
        blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
        if not blocks:
            return "<p></p>"
        rendered: list[str] = []
        for block in blocks:
            lines = [html.escape(line) for line in block.splitlines()]
            rendered.append(f"<p>{'<br/>'.join(lines)}</p>")
        return "".join(rendered)

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
