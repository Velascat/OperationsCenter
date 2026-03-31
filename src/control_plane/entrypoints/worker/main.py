from __future__ import annotations

import argparse

from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService
from control_plane.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Plane task through Kodo wrapper")
    parser.add_argument("--config", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )

    try:
        result = ExecutionService(settings).run_task(client, args.task_id)
        print(result.model_dump_json(indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
