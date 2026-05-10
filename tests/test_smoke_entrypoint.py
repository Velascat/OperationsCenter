# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
import json
import os
from pathlib import Path

import httpx

from operations_center.entrypoints.smoke import plane


def test_smoke_entrypoint_writes_retained_plane_payload(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "plane:",
                "  base_url: http://plane.local",
                "  api_token_env: PLANE_API_TOKEN",
                "  workspace_slug: ws",
                "  project_id: proj",
                "git:",
                "  provider: github",
                "kodo: {}",
                "repos: {}",
                f"report_root: {tmp_path / 'reports'}",
            ]
        )
    )
    monkeypatch.setenv("PLANE_API_TOKEN", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "TASK-9",
                    "project_id": "proj",
                    "name": "Smoke Task",
                    "description": """## Execution
repo: repo_a
base_branch: main
mode: goal

## Goal
Verify Plane access.
""",
                    "state": {"name": "Ready for AI"},
                    "labels": [],
                },
            )
        return httpx.Response(200, json={"ok": True})

    original_client = plane.PlaneClient

    class TestPlaneClient(original_client):
        def __init__(self, base_url: str, api_token: str, workspace_slug: str, project_id: str) -> None:
            super().__init__(base_url, api_token, workspace_slug, project_id)
            self._client = httpx.Client(
                transport=httpx.MockTransport(handler),
                base_url=base_url,
                headers={"X-API-Key": api_token, "Content-Type": "application/json"},
            )

    monkeypatch.setattr(plane, "PlaneClient", TestPlaneClient)
    monkeypatch.setattr(
        "sys.argv",
        [
            "plane-smoke",
            "--config",
            str(config_path),
            "--task-id",
            "TASK-9",
            "--comment-only",
        ],
    )

    plane.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    run_dir = Path(payload["artifacts"][0]).parent

    assert (run_dir / "plane_work_item.json").exists()
    assert (run_dir / "smoke_result.json").exists()
    assert payload["parsed_task"]["task_id"] == "TASK-9"

    os.environ.pop("PLANE_API_TOKEN", None)
