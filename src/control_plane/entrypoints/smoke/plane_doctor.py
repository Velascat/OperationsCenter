from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

from control_plane.config import load_settings


def request_summary(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.request(method, path, params=params)
    body_preview = response.text[:500]
    try:
        response.raise_for_status()
        ok = True
    except httpx.HTTPStatusError:
        ok = False
    return {
        "method": method,
        "path": path,
        "params": params or {},
        "status_code": response.status_code,
        "ok": ok,
        "body_preview": body_preview,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose Plane API access for current local config")
    parser.add_argument("--config", required=True)
    parser.add_argument("--task-id")
    args = parser.parse_args()

    settings = load_settings(args.config)
    headers = {"X-API-Key": settings.plane_token(), "Content-Type": "application/json"}
    client = httpx.Client(base_url=settings.plane.base_url, headers=headers, timeout=30.0)

    workspace_slug = settings.plane.workspace_slug
    project_id = settings.plane.project_id

    checks: list[dict[str, Any]] = []
    try:
        checks.append(request_summary(client, "GET", "/api/v1/users/me/"))
        checks.append(
            request_summary(
                client,
                "GET",
                f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/",
            )
        )
        checks.append(
            request_summary(
                client,
                "GET",
                f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/work-items/",
            )
        )
        checks.append(
            request_summary(
                client,
                "GET",
                f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/work-items/",
                params={"expand": "state"},
            )
        )
        if args.task_id:
            checks.append(
                request_summary(
                    client,
                    "GET",
                    f"/api/v1/workspaces/{workspace_slug}/projects/{project_id}/work-items/{args.task_id}/",
                )
            )

        user_resp = client.get("/api/v1/users/me/")
        user_name = None
        user_email = None
        if user_resp.status_code == 200:
            payload = user_resp.json()
            if isinstance(payload, dict):
                user_name = payload.get("display_name") or payload.get("first_name")
                user_email = payload.get("email")

        print(
            json.dumps(
                {
                    "base_url": settings.plane.base_url,
                    "workspace_slug": workspace_slug,
                    "project_id": project_id,
                    "api_user": {
                        "display_name": user_name,
                        "email": user_email,
                    },
                    "checks": checks,
                },
                indent=2,
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
