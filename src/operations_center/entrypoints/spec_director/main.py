# src/operations_center/entrypoints/spec_director/main.py
from __future__ import annotations

from typing import Any

import argparse
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.execution.usage_store import _check_disk_space
from operations_center.spec_director.brainstorm import BrainstormService
from operations_center.spec_director.campaign_builder import CampaignBuilder
from operations_center.spec_director.context_bundle import ContextBundleBuilder
from operations_center.spec_director.models import CampaignRecord
from operations_center.spec_director.phase_orchestrator import PhaseOrchestrator
from operations_center.spec_director.recovery import RecoveryService
from operations_center.spec_director.spec_writer import SpecWriter
from operations_center.spec_director.state import CampaignStateManager
from operations_center.spec_director.trigger import TriggerDetector

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_SPECS_DIR = Path("docs/specs")


def _count_state(issues: list[dict], state_name: str) -> int:
    """Count issues matching a board state name (case-insensitive)."""
    target = state_name.lower()
    return sum(
        1 for i in issues
        if str((i.get("state") or {}).get("name", "")).lower() == target
    )


def run_once(settings: Any, client: PlaneClient) -> None:
    sd = settings.spec_director
    if not sd.enabled:
        return

    logger.info(json.dumps({"event": "spec_cycle_start"}))

    state_mgr = CampaignStateManager()
    spec_writer = SpecWriter(specs_dir=_SPECS_DIR)
    spec_writer.archive_expired(retention_days=sd.spec_retention_days)

    # Step 1: Fetch all issues once — shared across orchestration, recovery, and trigger detection
    try:
        all_issues = client.list_issues()
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_board_fetch_failed", "error": str(exc)}))
        return

    # Step 2: Phase orchestration — advance phases, unblock tasks, detect completions
    orch = PhaseOrchestrator(
        client=client,
        state_manager=state_mgr,
        specs_dir=_SPECS_DIR,
    )
    orch_result = orch.run(all_issues)
    if any([
        orch_result.phases_advanced,
        orch_result.tasks_unblocked,
        orch_result.tasks_cancelled,
        orch_result.campaigns_completed,
    ]):
        logger.info(json.dumps({
            "event": "spec_phase_orchestration",
            "phases_advanced": orch_result.phases_advanced,
            "tasks_unblocked": orch_result.tasks_unblocked,
            "tasks_cancelled": orch_result.tasks_cancelled,
            "campaigns_completed": orch_result.campaigns_completed,
        }))

    # Step 3: Recovery scan (runs after orchestration so completions are already processed)
    active = state_mgr.load()
    recovery = RecoveryService(
        client=client,
        state_manager=state_mgr,
        abandon_hours=sd.campaign_abandon_hours,
    )
    for campaign in active.active_campaigns():
        if recovery.should_abandon(campaign):
            recovery.self_cancel(campaign, "abandon_hours_exceeded", _SPECS_DIR)
            logger.info(json.dumps({"event": "spec_campaign_abandoned", "campaign_id": campaign.campaign_id}))

    # Reload after potential cancellations
    active = state_mgr.load()

    # Step 4: Trigger detection — derive counts from already-fetched issues
    ready_count = _count_state(all_issues, "ready for ai")
    running_count = _count_state(all_issues, "in progress")
    trigger_detector = TriggerDetector(drop_file_path=Path(sd.drop_file_path))
    trigger = trigger_detector.detect(
        ready_count=ready_count,
        running_count=running_count,
        has_active_campaign=active.has_active(),
    )

    if trigger is None:
        logger.info(json.dumps({
            "event": "spec_no_trigger",
            "ready_count": ready_count,
            "running_count": running_count,
            "has_active": active.has_active(),
        }))
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

    # Step 5a: Build context bundle — reuse already-fetched issues
    available_repos = list(settings.repos.keys()) if settings.repos else []
    git_logs: dict[str, str] = {}
    for rk, rcfg in (settings.repos or {}).items():
        rpath = Path(rcfg.local_path) if getattr(rcfg, "local_path", None) else None
        git_logs[rk] = ContextBundleBuilder.collect_git_log(rpath) if rpath else ""

    bundle_builder = ContextBundleBuilder()
    specs_index = ContextBundleBuilder.collect_specs_index(_SPECS_DIR)
    bundle = bundle_builder.build(
        seed_text=trigger.seed_text,
        board_issues=all_issues,
        specs_index=specs_index,
        git_logs=git_logs,
        available_repos=available_repos,
    )

    # Step 5b: Brainstorm
    brainstorm_svc = BrainstormService(model=sd.brainstorm_model)
    try:
        result = brainstorm_svc.brainstorm(bundle)
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_brainstorm_failed", "error": str(exc)}))
        return

    # Step 5c: Write spec
    spec_path = spec_writer.write(slug=result.slug, spec_text=result.spec_text)

    # Determine repo from spec front matter
    from operations_center.spec_director.models import SpecFrontMatter
    available_repos = list(settings.repos.keys()) if settings.repos else []
    try:
        fm = SpecFrontMatter.from_spec_text(result.spec_text)
        spec_repo_key = fm.repos[0] if fm.repos else (available_repos[0] if available_repos else "")
    except Exception:
        spec_repo_key = available_repos[0] if available_repos else ""

    repo_cfg = settings.repos.get(spec_repo_key) if settings.repos else None
    base_branch = getattr(repo_cfg, "default_branch", "main") if repo_cfg else "main"

    # Step 5d: Create Plane campaign tasks
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

    # Step 5e: Record in state
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
