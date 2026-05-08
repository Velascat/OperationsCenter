# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
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


def _bootstrap_orphan_campaigns(
    settings: Any,
    client: PlaneClient,
    all_issues: list[dict],
    state_mgr: CampaignStateManager,
) -> None:
    """Create initial Plane tasks for active campaigns that have none.

    A campaign is "orphaned" if it appears in state/campaigns/active.json but no
    Plane work-item carries its `campaign-id: <id>` label. This happens when a
    campaign record is written outside the normal BrainstormService flow (e.g.
    pasted into active.json, recovered from spec front-matter, etc.).
    """
    from operations_center.spec_director.models import SpecFrontMatter

    by_campaign: dict[str, int] = {}
    for issue in all_issues:
        for label in issue.get("labels", []) or []:
            name = label.get("name", "") if isinstance(label, dict) else str(label)
            if name.lower().startswith("campaign-id:"):
                cid = name.split(":", 1)[1].strip()
                by_campaign[cid] = by_campaign.get(cid, 0) + 1

    active = state_mgr.load()
    builder = CampaignBuilder(
        client=client,
        project_id=settings.plane.project_id,
        max_tasks=settings.spec_director.max_tasks_per_campaign,
    )
    for campaign in active.active_campaigns():
        if by_campaign.get(campaign.campaign_id, 0) > 0:
            continue
        spec_path = _SPECS_DIR / f"{campaign.slug}.md"
        if not spec_path.exists():
            logger.warning(json.dumps({
                "event": "orphan_campaign_no_spec", "slug": campaign.slug,
            }, ensure_ascii=False))
            continue
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
            fm = SpecFrontMatter.from_spec_text(spec_text)
        except Exception as exc:
            logger.error(json.dumps({
                "event": "orphan_campaign_parse_failed",
                "slug": campaign.slug, "error": str(exc),
            }, ensure_ascii=False))
            continue
        repo_key = fm.repos[0] if fm.repos else ""
        repo_cfg = settings.repos.get(repo_key) if settings.repos else None
        if repo_cfg is None:
            logger.warning(json.dumps({
                "event": "orphan_campaign_unknown_repo",
                "slug": campaign.slug, "repo": repo_key,
            }, ensure_ascii=False))
            continue
        try:
            task_ids = builder.build(
                spec_text=spec_text,
                repo_key=repo_key,
                base_branch=repo_cfg.default_branch,
            )
            logger.info(json.dumps({
                "event": "orphan_campaign_bootstrapped",
                "campaign_id": campaign.campaign_id,
                "slug": campaign.slug,
                "tasks_created": len(task_ids),
            }, ensure_ascii=False))
        except Exception as exc:
            logger.error(json.dumps({
                "event": "orphan_campaign_bootstrap_failed",
                "slug": campaign.slug, "error": str(exc),
            }, ensure_ascii=False))


_LIFECYCLE_SKIP_PROMOTE = {"lifecycle: expanded", "lifecycle: archived", "lifecycle: escalated"}


def _auto_promote_backlog(client: PlaneClient, issues: list[dict]) -> None:
    """Promote tier-≥2 autonomy tasks from Backlog → Ready for AI each cycle.

    Filters out tasks carrying any lifecycle label that means "don't touch":
      • expanded   — work already delegated to children
      • archived   — terminal-and-frozen
      • escalated  — out of normal automated flow
    """
    from operations_center.autonomy_tiers.config import (
        get_family_tier, load_tiers_config,
    )
    from operations_center.proposer.backlog_promoter import BacklogPromoterService

    def _has_skip_label(issue: dict) -> bool:
        for lab in issue.get("labels", []) or []:
            name = (lab.get("name", "") if isinstance(lab, dict) else str(lab)).strip().lower()
            if name in _LIFECYCLE_SKIP_PROMOTE:
                return True
        return False

    filtered = [i for i in issues if not _has_skip_label(i)]

    tiers_config = load_tiers_config()
    service = BacklogPromoterService(
        plane_client=client,
        get_tier=lambda family: get_family_tier(family, tiers_config),
        dry_run=False,
    )
    try:
        result = service.promote(issues=filtered)
    except Exception as exc:
        logger.error(json.dumps({"event": "auto_promote_failed", "error": str(exc)}, ensure_ascii=False))
        return
    if result.promoted:
        logger.info(json.dumps({
            "event": "auto_promote_backlog",
            "count": len(result.promoted),
            "families": sorted({t.family for t in result.promoted}),
        }, ensure_ascii=False))


def run_once(settings: Any, client: PlaneClient) -> None:
    sd = settings.spec_director
    if not sd.enabled:
        return

    logger.info(json.dumps({"event": "spec_cycle_start"}, ensure_ascii=False))

    state_mgr = CampaignStateManager()
    spec_writer = SpecWriter(specs_dir=_SPECS_DIR)
    spec_writer.archive_expired(retention_days=sd.spec_retention_days)

    # Step 1: Fetch all issues once — shared across orchestration, recovery, and trigger detection
    try:
        all_issues = client.list_issues()
    except Exception as exc:
        logger.error(json.dumps({"event": "spec_board_fetch_failed", "error": str(exc)}, ensure_ascii=False))
        return

    # Step 1.5: Bootstrap orphan campaigns — campaigns registered in state but with
    # zero backing Plane tasks (e.g. autonomously-spawned campaigns whose builder
    # step never ran).  Without this, PhaseOrchestrator has nothing to advance.
    _bootstrap_orphan_campaigns(settings, client, all_issues, state_mgr)

    # Step 1.7: Auto-promote Backlog → Ready for AI for tasks whose family tier ≥ 2.
    # Tier bumps in autonomy_tiers.json apply to existing Backlog tasks via this hook.
    _auto_promote_backlog(client, all_issues)

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
        }, ensure_ascii=False))

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
            logger.info(json.dumps({"event": "spec_campaign_abandoned", "campaign_id": campaign.campaign_id}, ensure_ascii=False))

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
        }, ensure_ascii=False))
        return

    logger.info(json.dumps({
        "event": "spec_campaign_starting",
        "trigger_source": str(trigger.source),
        "seed_preview": trigger.seed_text[:80],
    }, ensure_ascii=False))

    # Disk space check before writing
    try:
        _check_disk_space(_SPECS_DIR)
    except OSError as exc:
        logger.error(json.dumps({"event": "spec_disk_space_critical", "error": str(exc)}, ensure_ascii=False))
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
        logger.error(json.dumps({"event": "spec_brainstorm_failed", "error": str(exc)}, ensure_ascii=False))
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
        logger.error(json.dumps({"event": "spec_campaign_build_failed", "error": str(exc)}, ensure_ascii=False))
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
    }, ensure_ascii=False))


def _write_heartbeat(status_dir: Path | None) -> None:
    if status_dir is None:
        return
    try:
        hb = status_dir / "heartbeat_spec.json"
        hb.write_text(
            json.dumps({"role": "spec", "at": datetime.now(UTC).isoformat(), "status": "idle"}),
            encoding="utf-8",
        )
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Spec director — autonomous spec-driven campaign manager")
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--status-dir", type=Path, default=None, help="Directory for heartbeat_spec.json")
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
            _write_heartbeat(args.status_dir)
            try:
                run_once(settings, client)
            except Exception as exc:
                logger.error(json.dumps({"event": "spec_director_cycle_error", "cycle": cycle, "error": str(exc)}, ensure_ascii=False))
            cycle += 1
            time.sleep(sd.poll_interval_seconds)
    finally:
        client.close()


if __name__ == "__main__":
    main()
