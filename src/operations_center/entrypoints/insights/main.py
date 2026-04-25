from __future__ import annotations

import argparse

from operations_center.insights.artifact_writer import InsightArtifactWriter
from operations_center.insights.derivers.commit_activity import CommitActivityDeriver
from operations_center.insights.derivers.dependency_drift import DependencyDriftDeriver
from operations_center.insights.derivers.dirty_tree import DirtyTreeDeriver
from operations_center.insights.derivers.arch_scheduler import ArchSchedulerDeriver
from operations_center.insights.derivers.backlog_promotion import BacklogPromotionDeriver
from operations_center.insights.derivers.execution_health import ExecutionHealthDeriver
from operations_center.insights.derivers.file_hotspots import FileHotspotsDeriver
from operations_center.insights.derivers.observation_coverage import ObservationCoverageDeriver
from operations_center.insights.derivers.test_continuity import TestContinuityDeriver
from operations_center.insights.derivers.todo_concentration import TodoConcentrationDeriver
from operations_center.insights.loader import SnapshotLoader
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.insights.service import InsightEngineService, new_generation_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate normalized insights from retained observer snapshots")
    parser.add_argument("--snapshot-run-id")
    parser.add_argument("--history-limit", type=int, default=5)
    parser.add_argument("--repo")
    args = parser.parse_args()

    from operations_center.tuning.applier import load_tuning_config

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
            "[warn] No observer snapshots found. "
            "Run 'operations-center.sh observe-repo' before generating insights.",
            flush=True,
        )
    elif age_hours > _stale_warn_hours:
        print(
            f"[warn] Latest observer snapshot is {age_hours:.1f}h old "
            f"(threshold: {_stale_warn_hours}h). "
            f"Insights may not reflect the current repo state. "
            f"Re-run 'operations-center.sh observe-repo' for fresh data.",
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
        source_command="operations-center generate-insights",
    )
    artifact, artifacts = service.generate(context)
    print(f"Insights artifact written: {artifacts[0]}")
    print(f"Insights generated: {len(artifact.insights)}")


if __name__ == "__main__":
    main()
