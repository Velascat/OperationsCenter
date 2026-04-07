from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from control_plane.adapters.plane import PlaneClient
from control_plane.config import load_settings
from control_plane.decision.artifact_writer import DecisionArtifactWriter
from control_plane.decision.loader import DecisionLoader
from control_plane.decision.service import ALL_FAMILIES, DecisionEngineService, _DEFAULT_ALLOWED_FAMILIES, new_decision_context
from control_plane.insights.artifact_writer import InsightArtifactWriter
from control_plane.insights.derivers.architecture_drift import ArchitectureDriftDeriver
from control_plane.insights.derivers.benchmark_regression import BenchmarkRegressionDeriver
from control_plane.insights.derivers.coverage_gap import CoverageGapDeriver
from control_plane.insights.derivers.execution_outcome import ExecutionOutcomeDeriver
from control_plane.insights.derivers.noop_loop import NoOpLoopDeriver
from control_plane.insights.derivers.quality_trend import QualityTrendDeriver
from control_plane.insights.derivers.theme_aggregation import ThemeAggregationDeriver
from control_plane.insights.derivers.commit_activity import CommitActivityDeriver
from control_plane.insights.derivers.cross_signal import CrossSignalDeriver
from control_plane.insights.derivers.dependency_drift import DependencyDriftDeriver
from control_plane.insights.derivers.dirty_tree import DirtyTreeDeriver
from control_plane.insights.derivers.execution_health import ExecutionHealthDeriver
from control_plane.insights.derivers.file_hotspots import FileHotspotsDeriver
from control_plane.insights.derivers.ci_pattern import CIPatternDeriver
from control_plane.insights.derivers.lint_drift import LintDriftDeriver
from control_plane.insights.derivers.security_vuln import SecurityVulnDeriver
from control_plane.insights.derivers.validation_pattern import ValidationPatternDeriver
from control_plane.insights.derivers.observation_coverage import ObservationCoverageDeriver
from control_plane.insights.derivers.proposal_outcome import ProposalOutcomeDeriver
from control_plane.insights.derivers.type_health import TypeHealthDeriver
from control_plane.insights.derivers.test_continuity import TestContinuityDeriver
from control_plane.insights.derivers.todo_concentration import TodoConcentrationDeriver
from control_plane.insights.loader import SnapshotLoader
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.insights.service import InsightEngineService, new_generation_context
from control_plane.observer.artifact_writer import ObserverArtifactWriter
from control_plane.observer.collectors.architecture_signal import ArchitectureSignalCollector
from control_plane.observer.collectors.benchmark_signal import BenchmarkSignalCollector
from control_plane.observer.collectors.coverage_signal import CoverageSignalCollector
from control_plane.observer.collectors.dependency_drift import DependencyDriftCollector
from control_plane.observer.collectors.execution_health import ExecutionArtifactCollector
from control_plane.observer.collectors.file_hotspots import FileHotspotsCollector
from control_plane.observer.collectors.git_context import GitContextCollector
from control_plane.observer.collectors.ci_history import CIHistoryCollector
from control_plane.observer.collectors.lint_signal import LintSignalCollector
from control_plane.observer.collectors.security_signal import SecuritySignalCollector
from control_plane.observer.collectors.validation_history import ValidationHistoryCollector
from control_plane.observer.collectors.recent_commits import RecentCommitsCollector
from control_plane.observer.collectors.type_check import TypeSignalCollector
from control_plane.observer.collectors.test_signal import TestSignalCollector
from control_plane.observer.collectors.todo_signal import TodoSignalCollector
from control_plane.observer.service import RepoObserverService, new_observer_context
from control_plane.observer.snapshot_builder import SnapshotBuilder
from control_plane.proposer import CandidateProposerIntegrationService
from control_plane.proposer.candidate_integration import new_proposer_integration_context

from control_plane.entrypoints.observer.main import configured_repo_match, ensure_git_repo, resolve_repo_path
from control_plane.observer.collectors.git_context import run_git


def build_observer_service() -> RepoObserverService:
    return RepoObserverService(
        repo_collector=GitContextCollector(),
        recent_commits_collector=RecentCommitsCollector(),
        file_hotspots_collector=FileHotspotsCollector(),
        test_signal_collector=TestSignalCollector(),
        dependency_drift_collector=DependencyDriftCollector(),
        todo_signal_collector=TodoSignalCollector(),
        execution_health_collector=ExecutionArtifactCollector(),
        lint_signal_collector=LintSignalCollector(),
        type_signal_collector=TypeSignalCollector(),
        ci_history_collector=CIHistoryCollector(),
        validation_history_collector=ValidationHistoryCollector(),
        architecture_signal_collector=ArchitectureSignalCollector(),
        benchmark_signal_collector=BenchmarkSignalCollector(),
        security_signal_collector=SecuritySignalCollector(),
        coverage_signal_collector=CoverageSignalCollector(),
        snapshot_builder=SnapshotBuilder(),
        artifact_writer=ObserverArtifactWriter(),
    )


def build_insight_service() -> InsightEngineService:
    from control_plane.tuning.applier import load_tuning_config

    tuning_config = load_tuning_config()
    normalizer = InsightNormalizer()
    validation_threshold = (
        tuning_config.get_int("execution_health", "validation_failure_threshold", 2)
        if tuning_config is not None
        else 2
    )
    return InsightEngineService(
        loader=SnapshotLoader(),
        derivers=[
            DirtyTreeDeriver(normalizer),
            CommitActivityDeriver(normalizer),
            FileHotspotsDeriver(normalizer),
            TestContinuityDeriver(normalizer),
            DependencyDriftDeriver(normalizer),
            TodoConcentrationDeriver(normalizer),
            ObservationCoverageDeriver(normalizer),
            ExecutionHealthDeriver(normalizer, validation_failure_threshold=validation_threshold),
            LintDriftDeriver(normalizer),
            TypeHealthDeriver(normalizer),
            CIPatternDeriver(normalizer),
            ValidationPatternDeriver(normalizer),
            ProposalOutcomeDeriver(normalizer),
            ArchitectureDriftDeriver(normalizer),
            BenchmarkRegressionDeriver(normalizer),
            SecurityVulnDeriver(normalizer),
            # CrossSignalDeriver runs last so all single-signal derivers have already fired.
            # Its insights are consumed by lint_fix and type_fix rules for confidence boosting.
            CrossSignalDeriver(normalizer),
            # Phase 4 — ExecutionOutcomeDeriver: reads retained execution transcripts
            # to classify failure modes (timeout_pattern, test_regression, validation_loop)
            ExecutionOutcomeDeriver(normalizer),
            # S8-7 — QualityTrendDeriver: tracks lint/type error deltas across snapshots
            QualityTrendDeriver(normalizer),
            # S9-3 — NoOpLoopDeriver: detects families cycling without acceptance
            NoOpLoopDeriver(normalizer),
            # S9-7 — CoverageGapDeriver: surfaces test coverage gaps
            CoverageGapDeriver(normalizer),
            # S9-10 — ThemeAggregationDeriver: groups persistent per-file violations
            ThemeAggregationDeriver(normalizer),
        ],
        artifact_writer=InsightArtifactWriter(),
    )


def build_decision_service() -> DecisionEngineService:
    from control_plane.tuning.applier import load_tuning_config
    tuning_config = load_tuning_config()
    return DecisionEngineService(
        loader=DecisionLoader(),
        artifact_writer=DecisionArtifactWriter(),
        tuning_config=tuning_config,
    )


# Deferred: Phase 7 — experiment mode entrypoint (see docs/design/roadmap.md §Phase 7)


def _write_quiet_diagnosis(
    report_dir: Path,
    *,
    quiet_window: int = 5,
    escalation_webhook: str = "",
    escalation_cooldown_seconds: int = 3600,
) -> None:
    """If the last *quiet_window* cycle reports all had 0 candidates emitted,
    write ``quiet_diagnosis.json`` aggregating suppression reasons.

    This gives the operator a structured answer to "why has the proposer
    produced nothing for N cycles?" without requiring manual log archaeology.
    """
    from collections import Counter

    cycle_reports = sorted(
        report_dir.glob("cycle_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:quiet_window]

    if len(cycle_reports) < quiet_window:
        return  # Not enough history yet

    all_zero = all(
        json.loads(p.read_text()).get("stages", {}).get("decide", {}).get("candidates_emitted", -1) == 0
        for p in cycle_reports
    )
    if not all_zero:
        # Remove stale diagnosis if conditions improved
        diag_path = report_dir / "quiet_diagnosis.json"
        diag_path.unlink(missing_ok=True)
        return

    # Aggregate suppression reasons across all quiet cycles
    reason_totals: Counter = Counter()
    families_seen: set[str] = set()
    for report_path in cycle_reports:
        try:
            report = json.loads(report_path.read_text())
            decide = report.get("stages", {}).get("decide", {})
            for reason, count in decide.get("suppression_reasons", {}).items():
                reason_totals[reason] += int(count)
            for family in decide.get("emitted_families", []):
                families_seen.add(family)
        except Exception:
            continue

    diag = {
        "generated_at": datetime.now(UTC).isoformat(),
        "quiet_window_cycles": quiet_window,
        "all_cycles_had_zero_candidates": True,
        "aggregated_suppression_reasons": dict(reason_totals.most_common()),
        "families_active_in_window": sorted(families_seen),
        "advice": (
            "The proposer has emitted no candidates for the last "
            f"{quiet_window} consecutive cycles.  Common causes: "
            "cooldown_active (run more cycles or reduce --cooldown-minutes), "
            "proposal_budget_too_low (executions are consuming capacity), "
            "existing_open_equivalent_task (similar tasks already on board), "
            "no emitted_families (all signal thresholds below decision rules)."
        ),
    }
    diag_path = report_dir / "quiet_diagnosis.json"
    diag_path.write_text(json.dumps(diag, indent=2))
    print(f"\n  [warn] Proposer quiet for {quiet_window} cycles — diagnosis → {diag_path}")
    # S7-7: Fire escalation webhook when proposer goes silent.
    if escalation_webhook:
        try:
            from control_plane.adapters.escalation import post_escalation
            from control_plane.execution.usage_store import UsageStore
            _now = datetime.now(UTC)
            _store = UsageStore()
            _should, _ids = _store.should_escalate(
                classification="proposer_quiet",
                threshold=1,
                cooldown_seconds=escalation_cooldown_seconds,
                now=_now,
            )
            if _should:
                post_escalation(
                    escalation_webhook,
                    classification="proposer_quiet",
                    count=quiet_window,
                    task_ids=[],
                    now=_now,
                )
                _store.record_escalation(
                    classification="proposer_quiet",
                    task_ids=[],
                    now=_now,
                )
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full observe→insights→decide→propose pipeline in one command. "
                    "Defaults to dry-run: shows what would be proposed without creating Plane tasks."
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--repo", help="Repo path or key (defaults to current directory)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Stop after decide stage and show candidates without creating tasks (default: on)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Run the full cycle including proposer (creates Plane tasks). Overrides --dry-run.",
    )
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--cooldown-minutes", type=int, default=120)
    parser.add_argument("--max-create", type=int, default=2)
    parser.add_argument(
        "--all-families",
        action="store_true",
        help="Enable all candidate families including hotspot_concentration and todo_accumulation "
             "(normally deferred until threshold analysis confirms value).",
    )
    args = parser.parse_args()

    dry_run = not args.execute

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    os.environ.setdefault("CONTROL_PLANE_CONFIG", args.config)
    settings = load_settings(args.config)

    # S6-1: Maintenance window gate — skip proposal creation when in window.
    _now_mw = datetime.now(UTC)
    for _w in getattr(settings, "maintenance_windows", []) or []:
        _days = list(getattr(_w, "days", []) or [])
        if _days and _now_mw.weekday() not in _days:
            continue
        _start = int(getattr(_w, "start_hour", 0))
        _end = int(getattr(_w, "end_hour", 0))
        _h = _now_mw.hour
        _in_window = (_start <= _h < _end) if _start < _end else (_h >= _start or _h < _end)
        if _in_window:
            print(
                f"[skip] Maintenance window active (hour={_h}, start={_start}, end={_end}). "
                "Proposal cycle suppressed."
            )
            return

    # --- Stage 1: Observe ---
    repo_path, repo_name = resolve_repo_path(args.repo, settings)
    ensure_git_repo(repo_path)
    configured_key, configured_base_branch = configured_repo_match(settings, repo_path)

    # Pull latest before observing so the snapshot reflects merged PRs
    try:
        run_git(["pull", "--ff-only"], repo_path)
    except Exception as exc:
        print(f"      [warn] git pull --ff-only failed: {exc} (observing local state as-is)")

    observer = build_observer_service()
    obs_context = new_observer_context(
        repo_path=repo_path,
        repo_name=configured_key or repo_name,
        base_branch=configured_base_branch,
        settings=settings,
        source_command="control-plane autonomy-cycle (observe)",
        commit_limit=10,
        hotspot_window=25,
        todo_limit=5,
        logs_root=Path("logs/local"),
    )
    snapshot, obs_artifacts = observer.observe(obs_context)
    print(f"[1/4] observe   → {obs_artifacts[0]}")
    if snapshot.collector_errors:
        print(f"      warnings: {len(snapshot.collector_errors)} collector error(s)")

    # --- Stage 2: Insights ---
    insight_svc = build_insight_service()
    ins_context = new_generation_context(
        repo_filter=args.repo,
        snapshot_run_id=snapshot.run_id,
        history_limit=5,
        source_command="control-plane autonomy-cycle (insights)",
    )
    insight_artifact, ins_artifacts = insight_svc.generate(ins_context)
    print(f"[2/4] insights  → {ins_artifacts[0]}  ({len(insight_artifact.insights)} insights)")

    # --- Stage 3: Decide ---
    allowed_families = ALL_FAMILIES if args.all_families else _DEFAULT_ALLOWED_FAMILIES
    decision_svc = build_decision_service()
    dec_context = new_decision_context(
        repo_filter=args.repo,
        insight_run_id=insight_artifact.run_id,
        history_limit=5,
        max_candidates=args.max_candidates,
        cooldown_minutes=args.cooldown_minutes,
        source_command="control-plane autonomy-cycle (decide)" + (" --dry-run" if dry_run else ""),
        dry_run=dry_run,
        allowed_families=allowed_families,
    )
    candidates_artifact, dec_artifacts = decision_svc.decide(dec_context)
    emitted = [c for c in candidates_artifact.candidates if c.status == "emit"]
    print(
        f"[3/4] decide    → {dec_artifacts[0]}"
        f"  ({len(emitted)} emitted, {len(candidates_artifact.suppressed)} suppressed)"
    )

    if emitted:
        print("\n  Candidates:")
        for c in emitted:
            print(f"    • [{c.family}] {c.proposal_outline.title_hint}")
    else:
        print("\n  No candidates emitted.")

    _esc_webhook = str(getattr(getattr(settings, "escalation", None), "webhook_url", "") or "")
    _esc_cooldown = int(getattr(getattr(settings, "escalation", None), "cooldown_seconds", 3600))

    if dry_run:
        print("\n  Dry-run mode: proposer stage skipped. Use --execute to create Plane tasks.")
        _write_cycle_report(
            snapshot=snapshot,
            insight_artifact=insight_artifact,
            candidates_artifact=candidates_artifact,
            emitted=emitted,
            prop_artifact=None,
            dry_run=True,
            escalation_webhook=_esc_webhook,
            escalation_cooldown_seconds=_esc_cooldown,
        )
        return

    # --- Stage 4: Propose ---
    if not emitted:
        print("\n[4/4] propose   → skipped (no emitted candidates)")
        _write_cycle_report(
            snapshot=snapshot,
            insight_artifact=insight_artifact,
            candidates_artifact=candidates_artifact,
            emitted=emitted,
            prop_artifact=None,
            dry_run=False,
            escalation_webhook=_esc_webhook,
            escalation_cooldown_seconds=_esc_cooldown,
        )
        return

    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    try:
        # Board saturation check: count queued autonomy tasks before creating more.
        # This mirrors the check in handle_propose_cycle for the watcher path.
        _max_queued = int(os.environ.get("CONTROL_PLANE_MAX_QUEUED_AUTONOMY_TASKS", "15"))
        try:
            _all_issues = client.list_issues()
            _autonomy_queued = sum(
                1 for _iss in _all_issues
                if str(_iss.get("state", {}).get("name", "") if isinstance(_iss.get("state"), dict) else "").strip()
                   in ("Ready for AI", "Backlog")
                and any(
                    "source: autonomy" == (str(lbl.get("name", "")) if isinstance(lbl, dict) else str(lbl)).strip().lower()
                    for lbl in _iss.get("labels", [])
                )
            )
            if _autonomy_queued >= _max_queued:
                print(
                    f"\n[4/4] propose   → skipped (board saturated: "
                    f"{_autonomy_queued} autonomy tasks queued, limit {_max_queued})"
                )
                _write_cycle_report(
                    snapshot=snapshot,
                    insight_artifact=insight_artifact,
                    candidates_artifact=candidates_artifact,
                    emitted=emitted,
                    prop_artifact=None,
                    dry_run=False,
                    escalation_webhook=_esc_webhook,
                    escalation_cooldown_seconds=_esc_cooldown,
                )
                return
        except Exception:
            pass  # Board count failure is non-fatal; proceed with propose

        proposer_svc = CandidateProposerIntegrationService(settings=settings, client=client)
        prop_context = new_proposer_integration_context(
            repo_filter=args.repo,
            decision_run_id=candidates_artifact.run_id,
            max_create=args.max_create,
            dry_run=False,
            source_command="control-plane autonomy-cycle (propose)",
        )
        prop_artifact, prop_artifacts = proposer_svc.run(prop_context)
        print(
            f"[4/4] propose   → {prop_artifacts[0]}"
            f"  (created={len(prop_artifact.created)}, skipped={len(prop_artifact.skipped)}, failed={len(prop_artifact.failed)})"
        )
        if prop_artifact.created:
            print("\n  Created tasks:")
            for r in prop_artifact.created:
                print(f"    • {r.plane_title} (id={r.plane_issue_id})")
        _write_cycle_report(
            snapshot=snapshot,
            insight_artifact=insight_artifact,
            candidates_artifact=candidates_artifact,
            emitted=emitted,
            prop_artifact=prop_artifact,
            dry_run=False,
            escalation_webhook=_esc_webhook,
            escalation_cooldown_seconds=_esc_cooldown,
        )
    finally:
        client.close()


def _write_cycle_report(
    *,
    snapshot,
    insight_artifact,
    candidates_artifact,
    emitted: list,
    prop_artifact,
    dry_run: bool,
    escalation_webhook: str = "",
    escalation_cooldown_seconds: int = 3600,
) -> None:
    from collections import Counter
    from control_plane.execution import UsageStore

    report_dir = Path("logs/autonomy_cycle")
    report_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"cycle_{ts}.json"

    # Disk space guardrail: skip writing if critically low to avoid partial files.
    try:
        from control_plane.execution.usage_store import _check_disk_space
        _check_disk_space(report_dir)
    except OSError as _disk_err:
        print(f"\n  [error] {_disk_err}")
        return

    # Signals collected summary
    signals = snapshot.signals
    signals_collected = []
    if signals.test_signal.status != "unknown":
        signals_collected.append("test_signal")
    if signals.dependency_drift.status not in ("unknown", "not_available"):
        signals_collected.append("dependency_drift")
    if signals.todo_signal.todo_count + signals.todo_signal.fixme_count > 0:
        signals_collected.append("todo_signal")
    if signals.execution_health.total_runs > 0:
        signals_collected.append("execution_health")
    if signals.backlog.items:
        signals_collected.append("backlog")
    if signals.lint_signal.status != "unavailable":
        signals_collected.append("lint_signal")
    if signals.type_signal.status != "unavailable":
        signals_collected.append("type_signal")
    if signals.ci_history.status != "unavailable":
        signals_collected.append("ci_history")
    if signals.validation_history.status != "unavailable":
        signals_collected.append("validation_history")

    # Insights by kind
    insights_by_kind: Counter = Counter(i.kind for i in insight_artifact.insights)

    # Suppression reason breakdown
    suppression_reasons: Counter = Counter(s.reason for s in candidates_artifact.suppressed)

    # Created task details
    created_tasks = []
    if prop_artifact is not None:
        for r in prop_artifact.created:
            created_tasks.append({
                "id": r.plane_issue_id,
                "title": r.plane_title,
                "family": r.family,
                "dedup_key": r.dedup_key,
            })

    # Guard rail summary
    usage_store = UsageStore()
    try:
        budget_remaining = usage_store.remaining_exec_capacity(now=datetime.now(UTC))
    except Exception:
        budget_remaining = None
    from control_plane.decision.service import _DEFAULT_ALLOWED_FAMILIES, ALL_FAMILIES
    gated_families = sorted(ALL_FAMILIES - _DEFAULT_ALLOWED_FAMILIES)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dry_run": dry_run,
        "repo": snapshot.repo.name,
        "stages": {
            "observe": {
                "run_id": snapshot.run_id,
                "collector_errors": len(snapshot.collector_errors),
                "signals_collected": signals_collected,
            },
            "insights": {
                "run_id": insight_artifact.run_id,
                "insights_emitted": len(insight_artifact.insights),
                "insights_by_kind": dict(insights_by_kind),
            },
            "decide": {
                "run_id": candidates_artifact.run_id,
                "candidates_emitted": len(emitted),
                "candidates_suppressed": len(candidates_artifact.suppressed),
                "suppression_reasons": dict(suppression_reasons),
                "emitted_families": [c.family for c in emitted],
                "emitted_candidates": [
                    {
                        "family": c.family,
                        "validation_profile": c.validation_profile,
                        "confidence": c.confidence,
                    }
                    for c in emitted
                ],
                # Deferred: Phase 4 — failure_classes_observed (see docs/design/roadmap.md §Phase 4)
            },
            "propose": {
                "run_id": prop_artifact.run_id if prop_artifact else None,
                "created": len(prop_artifact.created) if prop_artifact else 0,
                "skipped": len(prop_artifact.skipped) if prop_artifact else 0,
                "failed": len(prop_artifact.failed) if prop_artifact else 0,
                "tasks": created_tasks,
            },
        },
        "signals": {
            "test": signals.test_signal.status,
            "dependency_drift": signals.dependency_drift.status,
            "lint": {
                "status": signals.lint_signal.status,
                "violation_count": signals.lint_signal.violation_count,
            },
            "type": {
                "status": signals.type_signal.status,
                "error_count": signals.type_signal.error_count,
            },
            "ci": {
                "status": signals.ci_history.status,
                "failure_rate": signals.ci_history.failure_rate,
                "failing_checks": signals.ci_history.failing_checks,
                "flaky_checks": signals.ci_history.flaky_checks,
            },
            "validation_history": {
                "status": signals.validation_history.status,
                "tasks_analyzed": signals.validation_history.tasks_analyzed,
                "tasks_with_repeated_failures": len(signals.validation_history.tasks_with_repeated_failures),
                "overall_failure_rate": signals.validation_history.overall_failure_rate,
            },
            "execution_health": {
                "total_runs": signals.execution_health.total_runs,
                "no_op_count": signals.execution_health.no_op_count,
                "validation_failed_count": signals.execution_health.validation_failed_count,
            },
        },
        "guard_rail_summary": {
            "budget_remaining": budget_remaining,
            "families_gated": gated_families,
            "cycle_health": "nominal" if len(snapshot.collector_errors) == 0 else "degraded",
        },
    }
    report_path.write_text(json.dumps(report, indent=2))
    print(f"\n  Cycle report  → {report_path}")

    # Quiet diagnosis: if the last _QUIET_WINDOW cycle reports all had 0
    # candidates emitted, aggregate suppression reasons and write a diagnosis
    # file so the operator knows why the proposer went silent.
    _QUIET_WINDOW = 5
    _write_quiet_diagnosis(
        report_dir,
        quiet_window=_QUIET_WINDOW,
        escalation_webhook=escalation_webhook,
        escalation_cooldown_seconds=escalation_cooldown_seconds,
    )


if __name__ == "__main__":
    main()
