# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from operations_center.observer.models import ValidationFailureRecord, ValidationHistorySignal
from operations_center.observer.service import ObserverContext

_ARTIFACT_SCAN_LIMIT = 60
_MIN_RUNS_FOR_PATTERN = 2   # task must have at least this many runs to be flagged
_MIN_FAILURES_FOR_PATTERN = 2  # task must have at least this many validation failures to appear

# TODO (Phase 4 — per-profile failure tracking) [deferred, reviewed 2026-04-07]
# Extend this collector to track which validation_profile was expected when
# each failure occurred. Currently failures are counted per task but not
# classified by profile type.
# Implementation sketch:
#   1. Read validation_profile from task body or proposal_candidates artifact.
#   2. Extend ValidationFailureRecord with: validation_profile: str = ""
#   3. Group failures by (task_id, validation_profile) in task_stats.
#   4. Emit profile-specific failure insights from ValidationPatternDeriver.
# Unlock condition: ≥10 lint_fix executions with validation artifacts retained.
# See docs/design/roadmap.md §Phase 4.


class ValidationHistoryCollector:
    """Surface per-task validation failure patterns from retained execution artifacts.

    Unlike ExecutionArtifactCollector (which aggregates overall rates),
    this collector identifies which specific tasks have been retried multiple times
    and keep failing post-execution validation — a signal of systematic difficulty.
    """

    def __init__(self, *, artifact_scan_limit: int = _ARTIFACT_SCAN_LIMIT) -> None:
        self.artifact_scan_limit = artifact_scan_limit

    def collect(self, context: ObserverContext) -> ValidationHistorySignal:
        report_root = Path(context.settings.report_root)
        if not report_root.exists():
            return ValidationHistorySignal(status="unavailable", source="report_root_missing")

        run_dirs = sorted(
            [d for d in report_root.iterdir() if d.is_dir()],
            reverse=True,
        )[: self.artifact_scan_limit]

        # task_id → {worker_role, total_runs, validation_failures}
        task_stats: dict[str, dict] = defaultdict(
            lambda: {"worker_role": "unknown", "total_runs": 0, "validation_failures": 0}
        )

        total_runs = 0
        total_validation_failures = 0

        for run_dir in run_dirs:
            outcome_file = run_dir / "control_outcome.json"
            request_file = run_dir / "request.json"
            if not outcome_file.exists() or not request_file.exists():
                continue

            try:
                outcome = json.loads(outcome_file.read_text())
                request = json.loads(request_file.read_text())
            except Exception:
                continue

            task = request.get("task", {})
            repo_key = task.get("repo_key", "")
            if repo_key.lower() != context.repo_name.lower():
                continue

            outcome_status = str(outcome.get("status", "unknown"))
            if outcome_status not in ("executed", "no_op"):
                continue

            task_id = str(outcome.get("task_id", ""))
            if not task_id:
                continue

            worker_role = str(outcome.get("worker_role", "unknown"))
            task_stats[task_id]["worker_role"] = worker_role
            task_stats[task_id]["total_runs"] += 1
            total_runs += 1

            validation_file = run_dir / "validation.json"
            if validation_file.exists():
                try:
                    v = json.loads(validation_file.read_text())
                    if v.get("passed") is False:
                        task_stats[task_id]["validation_failures"] += 1
                        total_validation_failures += 1
                except Exception:
                    pass

        if total_runs == 0:
            return ValidationHistorySignal(status="unavailable", source="no_runs_found")

        repeated_failures: list[ValidationFailureRecord] = []
        for task_id, stats in task_stats.items():
            runs = stats["total_runs"]
            failures = stats["validation_failures"]
            failure_rate = failures / runs if runs > 0 else 0.0
            if (
                runs >= _MIN_RUNS_FOR_PATTERN
                and failures >= _MIN_FAILURES_FOR_PATTERN
                and failure_rate >= 0.5
            ):
                repeated_failures.append(
                    ValidationFailureRecord(
                        task_id=task_id,
                        worker_role=stats["worker_role"],
                        total_runs=runs,
                        validation_failure_count=failures,
                        failure_rate=round(failure_rate, 3),
                    )
                )

        # Sort by failure count descending
        repeated_failures.sort(key=lambda r: r.validation_failure_count, reverse=True)

        overall_failure_rate = total_validation_failures / total_runs if total_runs > 0 else 0.0
        status = "patterns_detected" if repeated_failures else "nominal"

        return ValidationHistorySignal(
            status=status,
            tasks_analyzed=len(task_stats),
            tasks_with_repeated_failures=repeated_failures,
            overall_failure_rate=round(overall_failure_rate, 3),
            source="execution_artifacts",
        )
