from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from control_plane.adapters.plane import PlaneClient
from control_plane.config import load_settings
from control_plane.decision.artifact_writer import DecisionArtifactWriter
from control_plane.decision.loader import DecisionLoader
from control_plane.decision.service import ALL_FAMILIES, DecisionEngineService, _DEFAULT_ALLOWED_FAMILIES, new_decision_context
from control_plane.insights.artifact_writer import InsightArtifactWriter
from control_plane.insights.derivers.commit_activity import CommitActivityDeriver
from control_plane.insights.derivers.dependency_drift import DependencyDriftDeriver
from control_plane.insights.derivers.dirty_tree import DirtyTreeDeriver
from control_plane.insights.derivers.file_hotspots import FileHotspotsDeriver
from control_plane.insights.derivers.observation_coverage import ObservationCoverageDeriver
from control_plane.insights.derivers.test_continuity import TestContinuityDeriver
from control_plane.insights.derivers.todo_concentration import TodoConcentrationDeriver
from control_plane.insights.loader import SnapshotLoader
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.insights.service import InsightEngineService, new_generation_context
from control_plane.observer.artifact_writer import ObserverArtifactWriter
from control_plane.observer.collectors.dependency_drift import DependencyDriftCollector
from control_plane.observer.collectors.file_hotspots import FileHotspotsCollector
from control_plane.observer.collectors.git_context import GitContextCollector
from control_plane.observer.collectors.recent_commits import RecentCommitsCollector
from control_plane.observer.collectors.test_signal import TestSignalCollector
from control_plane.observer.collectors.todo_signal import TodoSignalCollector
from control_plane.observer.service import RepoObserverService, new_observer_context
from control_plane.observer.snapshot_builder import SnapshotBuilder
from control_plane.proposer import CandidateProposerIntegrationService
from control_plane.proposer.candidate_integration import new_proposer_integration_context

from control_plane.entrypoints.observer.main import configured_repo_match, ensure_git_repo, resolve_repo_path


def build_observer_service() -> RepoObserverService:
    return RepoObserverService(
        repo_collector=GitContextCollector(),
        recent_commits_collector=RecentCommitsCollector(),
        file_hotspots_collector=FileHotspotsCollector(),
        test_signal_collector=TestSignalCollector(),
        dependency_drift_collector=DependencyDriftCollector(),
        todo_signal_collector=TodoSignalCollector(),
        snapshot_builder=SnapshotBuilder(),
        artifact_writer=ObserverArtifactWriter(),
    )


def build_insight_service() -> InsightEngineService:
    normalizer = InsightNormalizer()
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
        ],
        artifact_writer=InsightArtifactWriter(),
    )


def build_decision_service() -> DecisionEngineService:
    return DecisionEngineService(
        loader=DecisionLoader(),
        artifact_writer=DecisionArtifactWriter(),
    )


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

    # --- Stage 1: Observe ---
    repo_path, repo_name = resolve_repo_path(args.repo, settings)
    ensure_git_repo(repo_path)
    configured_key, configured_base_branch = configured_repo_match(settings, repo_path)

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

    if dry_run:
        print("\n  Dry-run mode: proposer stage skipped. Use --execute to create Plane tasks.")
        return

    # --- Stage 4: Propose ---
    if not emitted:
        print("\n[4/4] propose   → skipped (no emitted candidates)")
        return

    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    try:
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
    finally:
        client.close()


if __name__ == "__main__":
    main()
