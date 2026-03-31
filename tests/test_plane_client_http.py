import json

import httpx

from control_plane.adapters.plane import PlaneClient


def test_plane_comment_and_state_update_flow() -> None:
    calls: list[tuple[str, str, dict[str, object] | None, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode()) if request.content else None
        calls.append((request.method, str(request.url), payload, dict(request.headers)))

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
