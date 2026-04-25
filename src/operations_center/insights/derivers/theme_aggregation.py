"""ThemeAggregationDeriver — groups repeated per-file violations into architectural themes.

When the same source file appears in the top lint violations across ≥3 consecutive
observer snapshots, it signals a *structural* problem rather than a one-off fix.
Proposing N individual ``lint_fix`` tasks for that file wastes execution budget;
a single ``[Refactor]`` proposal that addresses the root cause is better.

Emits:
  theme/lint_cluster    — a file appears persistently in top lint violations
  theme/type_cluster    — a file appears persistently in top type errors
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot

_MIN_SNAPSHOTS = 3            # need this many snapshots to detect a pattern
_MIN_SNAPSHOT_APPEARANCES = 3  # file must appear in this many snapshots to qualify
_MAX_CLUSTER_FILES = 3         # emit at most this many cluster insights per run


class ThemeAggregationDeriver:
    """Groups persistent per-file violations into architectural theme insights."""

    def __init__(
        self,
        normalizer: InsightNormalizer,
        *,
        min_snapshots: int = _MIN_SNAPSHOTS,
        min_appearances: int = _MIN_SNAPSHOT_APPEARANCES,
        max_clusters: int = _MAX_CLUSTER_FILES,
    ) -> None:
        self.normalizer = normalizer
        self.min_snapshots = min_snapshots
        self.min_appearances = min_appearances
        self.max_clusters = max_clusters

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if len(snapshots) < self.min_snapshots:
            return []

        insights: list[DerivedInsight] = []
        first_seen = snapshots[-1].observed_at
        last_seen = snapshots[0].observed_at

        # Count how many snapshots each file appears in the top lint violations
        lint_file_appearances: Counter = Counter()
        for snap in snapshots:
            lint_sig = snap.signals.lint_signal
            if lint_sig.status not in ("violations",):
                continue
            seen_in_snap: set[str] = set()
            for v in lint_sig.top_violations:
                if v.path and v.path not in seen_in_snap:
                    lint_file_appearances[v.path] += 1
                    seen_in_snap.add(v.path)

        # Count how many snapshots each file appears in the top type errors
        type_file_appearances: Counter = Counter()
        for snap in snapshots:
            type_sig = snap.signals.type_signal
            if type_sig.status not in ("errors",):
                continue
            seen_in_snap = set()
            for e in type_sig.top_errors:
                if e.path and e.path not in seen_in_snap:
                    type_file_appearances[e.path] += 1
                    seen_in_snap.add(e.path)

        # Emit lint cluster insights for persistent files
        emitted = 0
        for fpath, count in lint_file_appearances.most_common():
            if count < self.min_appearances:
                break
            if emitted >= self.max_clusters:
                break
            insights.append(
                self.normalizer.normalize(
                    kind="theme/lint_cluster",
                    subject="theme",
                    status="present",
                    key_parts=["lint_cluster", fpath.replace("/", "_").replace(".", "_")],
                    evidence={
                        "file": fpath,
                        "snapshot_appearances": count,
                        "snapshots_analyzed": len(snapshots),
                        "theme_type": "lint_cluster",
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )
            emitted += 1

        # Emit type cluster insights for persistent files
        emitted = 0
        for fpath, count in type_file_appearances.most_common():
            if count < self.min_appearances:
                break
            if emitted >= self.max_clusters:
                break
            insights.append(
                self.normalizer.normalize(
                    kind="theme/type_cluster",
                    subject="theme",
                    status="present",
                    key_parts=["type_cluster", fpath.replace("/", "_").replace(".", "_")],
                    evidence={
                        "file": fpath,
                        "snapshot_appearances": count,
                        "snapshots_analyzed": len(snapshots),
                        "theme_type": "type_cluster",
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )
            emitted += 1

        return insights
