import json

import httpx

from control_plane.adapters.plane import PlaneClient


def test_plane_comment_and_state_update_flow() -> None:
    calls: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, str(request.url), payload))

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
    client._client = httpx.Client(transport=transport, base_url="http://plane.local")  # type: ignore[attr-defined]

    try:
        issue = client.fetch_issue("TASK-1")
        task = client.to_board_task(issue)
        client.transition_issue(task.task_id, "Running")
        client.comment_issue(task.task_id, "Hello\nworld")
    finally:
        client.close()

    assert any("/work-items/TASK-1/" in url for _, url, _ in calls)
    patch_call = next(payload for method, _, payload in calls if method == "PATCH")
    assert patch_call == {"state": "Running"}
    post_call = next(payload for method, _, payload in calls if method == "POST")
    assert isinstance(post_call, dict)
    assert "comment_html" in post_call
    assert "<p>Hello</p><p>world</p>" in str(post_call["comment_html"])
