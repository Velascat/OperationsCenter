from __future__ import annotations

import argparse
import logging
import time
from typing import Any

from control_plane.adapters.plane import PlaneClient
from control_plane.application import ExecutionService
from control_plane.config import load_settings


def issue_status_name(issue: dict[str, Any]) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", ""))
    return str(state or "")


def issue_task_kind(issue: dict[str, Any]) -> str:
    raw_labels = issue.get("labels", [])
    labels: list[str] = []
    if isinstance(raw_labels, list):
        for raw in raw_labels:
            if isinstance(raw, dict):
                name = raw.get("name")
                if name:
                    labels.append(str(name))
            elif raw:
                labels.append(str(raw))
    for label in labels:
        normalized = label.strip().lower()
        if normalized.startswith("task-kind:"):
            return normalized.split(":", 1)[1].strip()
    return "goal"


def select_ready_task_id(client: PlaneClient, ready_state: str = "Ready for AI", role: str = "goal") -> str:
    issues = client.list_issues()
    for issue in issues:
        task_id = str(issue["id"])
        status_name = issue_status_name(issue)
        task_kind = issue_task_kind(issue)
        if task_kind != role:
            continue
        if status_name == ready_state:
            return task_id
        if not status_name or status_name == str(issue.get("state", "")):
            detailed_issue = client.fetch_issue(task_id)
            if issue_task_kind(detailed_issue) == role and issue_status_name(detailed_issue) == ready_state:
                return task_id
    raise ValueError(f"No work item found in state '{ready_state}'")


def run_watch_loop(
    client: PlaneClient,
    service: ExecutionService,
    *,
    role: str,
    ready_state: str,
    poll_interval_seconds: int,
    max_cycles: int | None,
) -> None:
    logger = logging.getLogger(__name__)
    cycle = 0
    if role != "goal":
        raise ValueError(f"Worker role '{role}' is not implemented yet. MVP currently supports only 'goal'.")

    while True:
        cycle += 1
        logger.info(
            '{"event":"watch_cycle_start","role":"%s","cycle":%d,"poll_interval_seconds":%d}',
            role,
            cycle,
            poll_interval_seconds,
        )
        try:
            task_id = select_ready_task_id(client, ready_state=ready_state, role=role)
            logger.info('{"event":"watch_task_selected","role":"%s","cycle":%d,"task_id":"%s"}', role, cycle, task_id)
            issue = client.fetch_issue(task_id)
            if issue_status_name(issue) != ready_state or issue_task_kind(issue) != role:
                logger.info(
                    '{"event":"watch_task_skipped","role":"%s","cycle":%d,"task_id":"%s","reason":"state_or_kind_changed"}',
                    role,
                    cycle,
                    task_id,
                )
            else:
                client.transition_issue(task_id, "Running")
                logger.info('{"event":"watch_task_claimed","role":"%s","cycle":%d,"task_id":"%s"}', role, cycle, task_id)
                service.run_task(client, task_id)
        except ValueError as exc:
            logger.info(
                '{"event":"watch_no_task","role":"%s","cycle":%d,"message":"%s"}',
                role,
                cycle,
                str(exc).replace('"', "'"),
            )
        except Exception as exc:
            logger.info(
                '{"event":"watch_error","role":"%s","cycle":%d,"message":"%s"}',
                role,
                cycle,
                str(exc).replace('"', "'"),
            )

        if max_cycles is not None and cycle >= max_cycles:
            logger.info('{"event":"watch_complete","role":"%s","cycles":%d}', role, cycle)
            return
        time.sleep(poll_interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Plane task(s) through Kodo wrapper")
    parser.add_argument("--config", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--task-id")
    target.add_argument("--first-ready", action="store_true")
    target.add_argument("--watch", action="store_true")
    parser.add_argument("--ready-state", default="Ready for AI")
    parser.add_argument("--role", default="goal", choices=["goal", "test", "improve"])
    parser.add_argument("--poll-interval-seconds", type=int, default=15)
    parser.add_argument("--max-cycles", type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )

    try:
        service = ExecutionService(settings)
        if args.watch:
            run_watch_loop(
                client,
                service,
                role=args.role,
                ready_state=args.ready_state,
                poll_interval_seconds=args.poll_interval_seconds,
                max_cycles=args.max_cycles,
            )
            return

        task_id = args.task_id or select_ready_task_id(client, ready_state=args.ready_state, role=args.role)
        if args.first_ready:
            logging.getLogger(__name__).info(
                '{"event":"task_selected","mode":"first_ready","role":"%s","task_id":"%s"}',
                args.role,
                task_id,
            )
        result = service.run_task(client, task_id)
        print(result.model_dump_json(indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
