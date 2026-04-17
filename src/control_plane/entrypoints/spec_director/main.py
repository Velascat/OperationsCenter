# src/control_plane/entrypoints/spec_director/main.py
from __future__ import annotations

from typing import Any

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from control_plane.adapters.plane import PlaneClient
from control_plane.config import load_settings
from control_plane.execution.usage_store import _check_disk_space
from control_plane.spec_director.brainstorm import BrainstormService
from control_plane.spec_director.campaign_builder import CampaignBuilder
from control_plane.spec_director.context_bundle import ContextBundleBuilder
from control_plane.spec_director.models import CampaignRecord
from control_plane.spec_director.recovery import RecoveryService
from control_plane.spec_director.spec_writer import SpecWriter
from control_plane.spec_director.state import CampaignStateManager
from control_plane.spec_director.trigger import TriggerDetector

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_SPECS_DIR = Path("docs/specs")


def _count_ready_tasks(client: PlaneClient) -> int:
    try:
        issues = client.list_issues()
        return sum(
            1 for i in issues
            if str((i.get("state") or {}).get("name", "")).lower() == "ready for ai"
        )
    except Exception:
        return 99  # fail-safe: don't trigger queue drain on error


def _count_running_tasks(client: PlaneClient) -> int:
    try:
        issues = client.list_issues()
        return sum(
            1 for i in issues
            if str((i.get("state") or {}).get("name", "")).lower() == "in progress"
        )
    except Exception:
        return 99  # fail-safe: don't trigger queue drain on error


def _collect_board_issues(client: PlaneClient) -> list[dict]:
    try:
        return client.list_issues()
    except Exception:
        return []


def run_once(settings: Any, client: PlaneClient) -> None:
    sd = settings.spec_director
    if not sd.enabled:
        return

    logger.info(json.dumps({"event": "spec_cycle_start"}))

    state_mgr = CampaignStateManager()
    spec_writer = SpecWriter(specs_dir=_SPECS_DIR)

    # Rotate expired specs
    spec_writer.archive_expired(retention_days=sd.spec_retention_days)

    active = state_mgr.load()

    # Recovery scan
    _recovery = RecoveryService(
        client=client,
        state_manager=state_mgr,
        abandon_hours=sd.campaign_abandon_hours,
    )
    for campaign in active.active_campaigns():
        if _recovery.should_abandon(campaign):
            _recovery.self_cancel(campaign, "abandon_hours_exceeded", _SPECS_DIR)
            logger.info(json.dumps({"event": "spec_campaign_abandoned", "campaign_id": campaign.campaign_id}))

    # Reload after potential cancellations
    active = state_mgr.load()

    # Trigger detection
    trigger_detector = TriggerDetector(
        drop_file_path=Path(sd.drop_file_path),
    )
    ready_count = _count_ready_tasks(client)
    running_count = _count_running_tasks(client)
    trigger = trigger_detector.detect(
        ready_count=ready_count,
        running_count=running_count,
        has_active_campaign=active.has_active(),
    )

    if trigger is None:
        logger.info(json.dumps({"event": "spec_no_trigger", "ready_count": ready_count, "running_count": running_count, "has_active": active.has_active()}))
        return

    logger.info(json.dumps({
        "event": "spec_campaign_starting",
        "trigger_source": str(trigger.source),
        "seed_preview": trigger.seed_text[:80],
    }))

    # Disk space check before writing
    try:
        _check_disk_space(_SPECS_DIR)
    except OSError as exc:
        logger.error(json.dumps({"event": "spec_disk_space_critical", "error": str(exc)}))
        return

    # Build context bundle
    available_repos = list(settings.repos.keys()) if settings.repos else []
    git_logs: dict[str, str] = {}
    for rk, rcfg in (settings.repos or {}).items():
        rpath = Path(rcfg.local_path) if getattr(rcfg, "local_path", None) else None
        git_logs[rk] = ContextBundleBuilder.collect_git_log(rpath) if rpath else ""

    bundle_builder = ContextBundleBuilder()
    specs_index = ContextBundleBuilder.collect_specs_index(_SPECS_DIR)
    board_issues = _collect_board_issues(client)
    bundle = bundle_builder.build(
        seed_text=trigger.seed_text,
        board_issues=board_issues,
        specs_index=specs_index,
        git_logs=git_logs,
        available_repos=available_repos,
    )

    # Brainstorm
    brainstorm_svc = BrainstormService(model=sd.brainstorm_model)
    try:
        result = brainstorm_svc.brainstorm(bundle)
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_brainstorm_failed", "error": str(exc)}))
        return

    # Write spec
    spec_path = spec_writer.write(slug=result.slug, spec_text=result.spec_text)

    # Determine repo from spec front matter
    from control_plane.spec_director.models import SpecFrontMatter
    available_repos = list(settings.repos.keys()) if settings.repos else []
    try:
        fm = SpecFrontMatter.from_spec_text(result.spec_text)
        spec_repo_key = fm.repos[0] if fm.repos else (available_repos[0] if available_repos else "")
    except Exception:
        spec_repo_key = available_repos[0] if available_repos else ""

    repo_cfg = settings.repos.get(spec_repo_key) if settings.repos else None
    base_branch = getattr(repo_cfg, "default_branch", "main") if repo_cfg else "main"

    # Create Plane campaign tasks
    builder = CampaignBuilder(
        client=client,
        project_id=settings.plane.project_id,
        max_tasks=sd.max_tasks_per_campaign,
    )
    try:
        task_ids = builder.build(
            spec_text=result.spec_text,
            repo_key=spec_repo_key,
            base_branch=base_branch,
        )
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_campaign_build_failed", "error": str(exc)}))
        spec_path.unlink(missing_ok=True)
        return

    # Record in state
    campaign_record = CampaignRecord(
        campaign_id=result.campaign_id,
        slug=result.slug,
        spec_file=str(spec_path),
        status="active",
        created_at=datetime.now(UTC).isoformat(),
    )
    state_mgr.add_campaign(campaign_record)

    # Archive drop-file only after successful campaign creation
    if hasattr(trigger.source, "value") and trigger.source.value == "drop_file":
        trigger_detector.archive_drop_file()

    logger.info(json.dumps({
        "event": "spec_campaign_created",
        "campaign_id": result.campaign_id,
        "slug": result.slug,
        "tasks_created": len(task_ids),
    }))


def main() -> None:
    parser = argparse.ArgumentParser(description="Spec director — autonomous spec-driven campaign manager")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    sd = settings.spec_director

    try:
        if args.once:
            run_once(settings, client)
            return
        cycle = 0
        while True:
            try:
                run_once(settings, client)
            except Exception as exc:
                logger.error(json.dumps({"event": "spec_director_cycle_error", "cycle": cycle, "error": str(exc)}))
            cycle += 1
            time.sleep(sd.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    main()
