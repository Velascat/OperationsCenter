from __future__ import annotations

import argparse

from control_plane.insights.artifact_writer import InsightArtifactWriter
from control_plane.insights.derivers.commit_activity import CommitActivityDeriver
from control_plane.insights.derivers.dependency_drift import DependencyDriftDeriver
from control_plane.insights.derivers.dirty_tree import DirtyTreeDeriver
from control_plane.insights.derivers.arch_scheduler import ArchSchedulerDeriver
from control_plane.insights.derivers.backlog_promotion import BacklogPromotionDeriver
from control_plane.insights.derivers.execution_health import ExecutionHealthDeriver
from control_plane.insights.derivers.file_hotspots import FileHotspotsDeriver
from control_plane.insights.derivers.observation_coverage import ObservationCoverageDeriver
from control_plane.insights.derivers.test_continuity import TestContinuityDeriver
from control_plane.insights.derivers.todo_concentration import TodoConcentrationDeriver
from control_plane.insights.loader import SnapshotLoader
from control_plane.insights.normalizer import InsightNormalizer
from control_plane.insights.service import InsightEngineService, new_generation_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate normalized insights from retained observer snapshots")
    parser.add_argument("--snapshot-run-id")
    parser.add_argument("--history-limit", type=int, default=5)
    parser.add_argument("--repo")
    args = parser.parse_args()

    from control_plane.tuning.applier import load_tuning_config

    tuning_config = load_tuning_config()
    normalizer = InsightNormalizer()
    validation_threshold = (
        tuning_config.get_int("execution_health", "validation_failure_threshold", 2)
        if tuning_config is not None
        else 2
    )
    loader = SnapshotLoader()
    # Warn when the latest observer snapshot is stale (> 2 hours old).
    # Stale snapshots mean insights are derived from an outdated repo view.
    _stale_warn_hours = 2.0
    age_hours = loader.latest_snapshot_age_hours(repo=args.repo)
    if age_hours is None:
        print(
            f"[warn] No observer snapshots found. "
            f"Run 'control-plane.sh observe-repo' before generating insights.",
            flush=True,
        )
    elif age_hours > _stale_warn_hours:
        print(
            f"[warn] Latest observer snapshot is {age_hours:.1f}h old "
            f"(threshold: {_stale_warn_hours}h). "
            f"Insights may not reflect the current repo state. "
            f"Re-run 'control-plane.sh observe-repo' for fresh data.",
            flush=True,
        )

    service = InsightEngineService(
        loader=loader,
        derivers=[
            DirtyTreeDeriver(normalizer),
            CommitActivityDeriver(normalizer),
            FileHotspotsDeriver(normalizer),
            TestContinuityDeriver(normalizer),
            DependencyDriftDeriver(normalizer),
            TodoConcentrationDeriver(normalizer),
            ObservationCoverageDeriver(normalizer),
            ExecutionHealthDeriver(normalizer, validation_failure_threshold=validation_threshold),
            BacklogPromotionDeriver(normalizer),
            ArchSchedulerDeriver(normalizer),
        ],
        artifact_writer=InsightArtifactWriter(),
    )
    repo_filter = args.repo
    context = new_generation_context(
        repo_filter=repo_filter,
        snapshot_run_id=args.snapshot_run_id,
        history_limit=args.history_limit,
        source_command="control-plane generate-insights",
    )
    artifact, artifacts = service.generate(context)
    print(f"Insights artifact written: {artifacts[0]}")
    print(f"Insights generated: {len(artifact.insights)}")


if __name__ == "__main__":
    main()
