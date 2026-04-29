# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import argparse
import json
import logging
import traceback
import uuid

from operations_center.adapters.plane import PlaneClient
from operations_center.adapters.reporting import Reporter
from operations_center.config import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test Plane integration for one work item")
    parser.add_argument("--config", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--comment-only", action="store_true")
    parser.add_argument("--comment-text", default="Operations Center smoke test comment")
    parser.add_argument("--transition-to")
    parser.add_argument("--restore-state")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger(__name__)

    settings = load_settings(args.config)
    reporter = Reporter(settings.report_root)
    run_id = uuid.uuid4().hex[:12]
    run_dir = reporter.create_run_dir(args.task_id, run_id)
    artifacts = [reporter.write_request_context(run_dir, args.task_id, run_id, phase="smoke_initialized")]

    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )

    fetched = False
    parsed = False
    comment_posted = False

    try:
        issue = client.fetch_issue(args.task_id)
        fetched = True
        artifacts.append(reporter.write_plane_payload(run_dir, issue))

        task = client.to_board_task(issue)
        parsed = True

        if args.comment_only:
            client.comment_issue(args.task_id, args.comment_text)
            comment_posted = True

        if args.transition_to:
            client.transition_issue(args.task_id, args.transition_to)
        if args.restore_state:
            client.transition_issue(args.task_id, args.restore_state)

        artifacts.append(
            reporter.write_smoke_result(
                run_dir,
                task_id=args.task_id,
                fetched=fetched,
                parsed=parsed,
                comment_posted=comment_posted,
                transition_state=args.transition_to,
                restore_state=args.restore_state,
            )
        )

        logger.info(json.dumps({"event": "plane_smoke_complete", "run_id": run_id, "task_id": args.task_id}))
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "task_id": args.task_id,
                    "parsed_task": task.model_dump(),
                    "artifacts": artifacts,
                },
                indent=2,
            )
        )
    except Exception:
        reporter.write_failure(run_dir, traceback.format_exc(), phase="smoke")
        logger.info(json.dumps({"event": "plane_smoke_failed", "run_id": run_id, "task_id": args.task_id}))
        raise
    finally:
        client.close()


if __name__ == "__main__":
    main()
