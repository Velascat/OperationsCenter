# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ExecutionOutcomeDeriver — Phase 4 execution feedback depth.

Reads retained kodo_plane execution artifacts to classify failure patterns
across recent runs and emit structured insights.  Unlike ExecutionHealthDeriver
(which only sees aggregate success/failure counts), this deriver reads the
actual execution transcripts and classifies *why* executions failed.

Emits insights:
  - execution_outcome/timeout_pattern   — ≥2 timeout failures in the window
  - execution_outcome/test_regression   — test-failure pattern (not lint/type)
  - execution_outcome/validation_loop   — task blocked repeatedly on same validator
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from operations_center.insights.models import DerivedInsight
from operations_center.insights.normalizer import InsightNormalizer
from operations_center.observer.models import RepoStateSnapshot

_ARTIFACT_SCAN_LIMIT = 50
_TIMEOUT_TOKENS = frozenset({"timed out", "timeout", "deadline exceeded", "operation timed out"})
_TEST_REGRESSION_TOKENS = frozenset({"test failed", "assertion error", "assertionerror", "failed test"})
_VALIDATION_LOOP_MIN_REPEATS = 3


class ExecutionOutcomeDeriver:
    """Derives failure-pattern insights from retained execution transcripts.

    Reads ``control_outcome.json`` and ``stderr.txt`` (or ``stdout.txt``) from
    ``tools/report/kodo_plane/`` run directories.  Falls back gracefully when
    artifacts are absent.
    """

    def __init__(self, normalizer: InsightNormalizer, *, artifact_root: Path | None = None) -> None:
        self.normalizer = normalizer
        self._artifact_root = artifact_root or Path("tools/report/kodo_plane")

    def derive(self, snapshots: Sequence[RepoStateSnapshot]) -> list[DerivedInsight]:
        if not snapshots:
            return []
        # Use the most recent snapshot's repo name to filter artifacts
        repo_name = snapshots[0].repo.name

        timeout_count = 0
        test_regression_count = 0
        validation_fail_by_task: Counter[str] = Counter()
        first_seen = snapshots[-1].observed_at
        last_seen = snapshots[0].observed_at

        if not self._artifact_root.exists():
            return []

        run_dirs = sorted(
            [d for d in self._artifact_root.iterdir() if d.is_dir()],
            reverse=True,
        )[:_ARTIFACT_SCAN_LIMIT]

        for run_dir in run_dirs:
            outcome_file = run_dir / "control_outcome.json"
            request_file = run_dir / "request.json"
            if not outcome_file.exists() or not request_file.exists():
                continue
            try:
                import json
                outcome = json.loads(outcome_file.read_text(encoding="utf-8"))
                request = json.loads(request_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            task = request.get("task", {})
            if task.get("repo_key", "").lower() != repo_name.lower():
                continue

            if outcome.get("outcome_status") not in ("blocked", "error"):
                continue

            # Read stderr/stdout for pattern matching
            stderr = ""
            for candidate in ("stderr.txt", "stdout.txt", "kodo_stderr.txt"):
                p = run_dir / candidate
                if p.exists():
                    try:
                        stderr = p.read_text(encoding="utf-8", errors="replace").lower()
                    except Exception:
                        pass
                    break

            blocked_classification = outcome.get("blocked_classification", "")

            # Timeout pattern
            if blocked_classification == "timeout" or any(tok in stderr for tok in _TIMEOUT_TOKENS):
                timeout_count += 1

            # Test regression pattern (validation failed on test run, not lint/type)
            if blocked_classification == "validation_failure":
                if any(tok in stderr for tok in _TEST_REGRESSION_TOKENS):
                    test_regression_count += 1
                # Validation loop: same task failed validation repeatedly
                task_id = str(task.get("task_id", ""))
                if task_id:
                    validation_fail_by_task[task_id] += 1

        insights: list[DerivedInsight] = []

        if timeout_count >= 2:
            insights.append(
                self.normalizer.normalize(
                    kind="execution_outcome/timeout_pattern",
                    subject="execution_outcome",
                    status="present",
                    key_parts=["timeout_pattern"],
                    evidence={"timeout_count": timeout_count, "window": _ARTIFACT_SCAN_LIMIT},
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )

        if test_regression_count >= 2:
            insights.append(
                self.normalizer.normalize(
                    kind="execution_outcome/test_regression",
                    subject="execution_outcome",
                    status="present",
                    key_parts=["test_regression"],
                    evidence={"test_regression_count": test_regression_count},
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )

        looping_tasks = [tid for tid, cnt in validation_fail_by_task.items()
                         if cnt >= _VALIDATION_LOOP_MIN_REPEATS]
        if looping_tasks:
            insights.append(
                self.normalizer.normalize(
                    kind="execution_outcome/validation_loop",
                    subject="execution_outcome",
                    status="present",
                    key_parts=["validation_loop"],
                    evidence={
                        "looping_task_count": len(looping_tasks),
                        "task_ids": looping_tasks[:5],
                    },
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                )
            )

        return insights
