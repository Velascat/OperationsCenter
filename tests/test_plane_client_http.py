import json

import httpx

from control_plane.adapters.plane import PlaneClient


def test_plane_comment_and_state_update_flow() -> None:
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, str(request.url), payload, dict(request.headers)))

        if request.method == "GET" and "/states/" in str(request.url):
            return httpx.Response(200, json={"results": []})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "TASK-1",
                    "project_id": "proj",
                    "name": "Task",
                    "description": """## Execution
repo: repo_a
base_branch: main
mode: goal

## Goal
Do thing.
""",
                    "state": {"name": "Ready for AI"},
                    "labels": [],
                },
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    client._client = httpx.Client(  # type: ignore[attr-defined]
        transport=transport,
        base_url="http://plane.local",
        headers={"X-API-Key": "token", "Content-Type": "application/json"},
    )

    try:
        issue = client.fetch_issue("TASK-1")
        task = client.to_board_task(issue)
        client.transition_issue(task.task_id, "Running")
        client.comment_issue(task.task_id, "Result\n- success: true\n- run_id: abc")
    finally:
        client.close()

    assert any("/work-items/TASK-1/" in url for _, url, _, _ in calls)
    patch_call = next(payload for method, _, payload, _ in calls if method == "PATCH")
    assert patch_call == {"state": "Running"}

    post_call = next(payload for method, _, payload, _ in calls if method == "POST")
    assert isinstance(post_call, dict)
    assert "comment_html" in post_call
    assert "<ul><li>success: true</li><li>run_id: abc</li></ul>" in str(post_call["comment_html"])

    for _, _, _, headers in calls:
        assert headers.get("x-api-key") == "token"


def test_plane_fetch_project_uses_workspace_and_project_path() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"id": "proj", "name": "Engineering"})

    transport = httpx.MockTransport(handler)
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    client._client = httpx.Client(  # type: ignore[attr-defined]
        transport=transport,
        base_url="http://plane.local",
        headers={"X-API-Key": "token", "Content-Type": "application/json"},
    )

    try:
        project = client.fetch_project()
    finally:
        client.close()

    assert project["name"] == "Engineering"
    assert calls == [("GET", "http://plane.local/api/v1/workspaces/ws/projects/proj/")]


def test_plane_list_issues_supports_paginated_results() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json={
                "results": [
                    {"id": "TASK-1", "state": {"name": "Ready for AI"}},
                    {"id": "TASK-2", "state": {"name": "Backlog"}},
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    client._client = httpx.Client(  # type: ignore[attr-defined]
        transport=transport,
        base_url="http://plane.local",
        headers={"X-API-Key": "token", "Content-Type": "application/json"},
    )

    try:
        issues = client.list_issues()
    finally:
        client.close()

    assert [issue["id"] for issue in issues] == ["TASK-1", "TASK-2"]
    assert calls == [("GET", "http://plane.local/api/v1/workspaces/ws/projects/proj/work-items/?expand=state")]


def test_plane_fetch_issue_hydrates_label_ids_to_label_objects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/labels/"):
            return httpx.Response(200, json={"results": [{"id": "LABEL-1", "name": "task-kind: improve"}]})
        return httpx.Response(
            200,
            json={
                "id": "TASK-1",
                "project_id": "proj",
                "name": "Task",
                "description": """## Execution
repo: repo_a
base_branch: main
mode: goal

## Goal
Do thing.
""",
                "state": {"name": "Ready for AI"},
                "labels": ["LABEL-1"],
            },
        )

    transport = httpx.MockTransport(handler)
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    client._client = httpx.Client(  # type: ignore[attr-defined]
        transport=transport,
        base_url="http://plane.local",
        headers={"X-API-Key": "token", "Content-Type": "application/json"},
    )

    try:
        issue = client.fetch_issue("TASK-1")
    finally:
        client.close()

    assert issue["labels"] == [{"id": "LABEL-1", "name": "task-kind: improve"}]


def test_plane_create_issue_ensures_labels_and_state() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, str(request.url), payload))
        url = str(request.url)
        if request.method == "GET" and "/states/" in url:
            return httpx.Response(200, json={"results": [{"id": "STATE-1", "name": "Ready for AI"}]})
        if request.method == "GET" and "/labels/" in url:
            return httpx.Response(200, json={"results": []})
        if request.method == "POST" and url.endswith("/labels/"):
            assert payload == {"name": "task-kind: goal"} or payload == {"name": "source: improve-worker"}
            return httpx.Response(201, json={"id": f"LABEL-{len([c for c in calls if c[1].endswith('/labels/') and c[0] == 'POST'])}"})
        if request.method == "POST" and url.endswith("/work-items/"):
            return httpx.Response(201, json={"id": "TASK-NEW", "name": payload["name"]})
        raise AssertionError(f"Unexpected call: {request.method} {url}")

    transport = httpx.MockTransport(handler)
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    client._client = httpx.Client(  # type: ignore[attr-defined]
        transport=transport,
        base_url="http://plane.local",
        headers={"X-API-Key": "token", "Content-Type": "application/json"},
    )

    try:
        created = client.create_issue(
            name="Follow-up",
            description="## Goal\nDo thing.",
            state="Ready for AI",
            label_names=["task-kind: goal", "source: improve-worker"],
        )
    finally:
        client.close()

    assert created["id"] == "TASK-NEW"
    work_item_payload = next(payload for method, url, payload in calls if method == "POST" and url.endswith("/work-items/"))
    assert work_item_payload["state"] == "STATE-1"
    assert work_item_payload["labels"] == ["LABEL-1", "LABEL-2"]
    assert "description_html" in work_item_payload


def test_plane_list_comments_supports_paginated_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"id": "C-1", "comment_html": "<p>Hello</p>"}]})

    transport = httpx.MockTransport(handler)
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    client._client = httpx.Client(  # type: ignore[attr-defined]
        transport=transport,
        base_url="http://plane.local",
        headers={"X-API-Key": "token", "Content-Type": "application/json"},
    )

    try:
        comments = client.list_comments("TASK-1")
    finally:
        client.close()

    assert comments == [{"id": "C-1", "comment_html": "<p>Hello</p>"}]


def test_plane_task_parses_from_description_html_when_plain_text_missing() -> None:
    client = PlaneClient("http://plane.local", "token", "ws", "proj")
    issue = {
        "id": "TASK-9",
        "project_id": "proj",
        "name": "Task",
        "description_html": """
<h2>Execution</h2>
<p>repo: repo_a
<br/>base_branch: main
<br/>mode: goal</p>
<h2>Goal</h2>
<p>Do thing.</p>
<h2>Constraints</h2>
<ul><li>Keep tests green.</li></ul>
""",
        "state": {"name": "Ready for AI"},
        "labels": [],
    }
    try:
        task = client.to_board_task(issue)
    finally:
        client.close()

    assert task.repo_key == "repo_a"
    assert task.base_branch == "main"
    assert task.goal_text == "Do thing."
    assert task.constraints_text == "- Keep tests green."
