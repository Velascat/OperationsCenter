# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json
from pathlib import Path

from operations_center.observer.models import ExecutionHealthSignal, ExecutionRunRecord
from operations_center.observer.service import ObserverContext

_ARTIFACT_SCAN_LIMIT = 60
_RECENT_RUNS_IN_SIGNAL = 10


class ExecutionArtifactCollector:
    """Reads retained kodo_plane execution artifacts for a specific repo and
    surfaces execution health metrics (no_op rate, validation failure rate)
    that feed the downstream insight → decision → propose pipeline."""

    def __init__(self, *, artifact_scan_limit: int = _ARTIFACT_SCAN_LIMIT) -> None:
        self.artifact_scan_limit = artifact_scan_limit

    def collect(self, context: ObserverContext) -> ExecutionHealthSignal:
        report_root = Path(context.settings.report_root)
        if not report_root.exists():
            return ExecutionHealthSignal()

        run_dirs = sorted(
            [d for d in report_root.iterdir() if d.is_dir()],
            reverse=True,
        )[: self.artifact_scan_limit]

        total = 0
        executed = 0
        no_op = 0
        unknown = 0
        error = 0
        validation_failed = 0
        recent_runs: list[ExecutionRunRecord] = []

        for run_dir in run_dirs:
            outcome_file = run_dir / "control_outcome.json"
            request_file = run_dir / "request.json"
            if not outcome_file.exists() or not request_file.exists():
                continue

            try:
                outcome = json.loads(outcome_file.read_text(encoding="utf-8"))
                request = json.loads(request_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            task = request.get("task", {})
            repo_key = task.get("repo_key", "")
            if repo_key.lower() != context.repo_name.lower():
                continue

            validation_passed: bool | None = None
            validation_file = run_dir / "validation.json"
            if validation_file.exists():
                try:
                    v = json.loads(validation_file.read_text(encoding="utf-8"))
                    raw = v.get("passed")
                    if raw is not None:
                        validation_passed = bool(raw)
                except Exception:
                    pass

            outcome_status = str(outcome.get("status", "unknown"))
            outcome_reason = outcome.get("reason")
            if outcome_reason is not None:
                outcome_reason = str(outcome_reason)
            task_id = str(outcome.get("task_id", ""))
            worker_role = str(outcome.get("worker_role", "unknown"))
            run_id = str(request.get("run_id", run_dir.name))

            total += 1
            if outcome_status == "executed":
                executed += 1
                if validation_passed is False:
                    validation_failed += 1
            elif outcome_status == "no_op":
                no_op += 1
            elif outcome_status == "unknown":
                unknown += 1
            elif outcome_status == "error":
                error += 1

            recent_runs.append(
                ExecutionRunRecord(
                    run_id=run_id,
                    task_id=task_id,
                    worker_role=worker_role,
                    outcome_status=outcome_status,
                    outcome_reason=outcome_reason,
                    validation_passed=validation_passed,
                )
            )

        return ExecutionHealthSignal(
            total_runs=total,
            executed_count=executed,
            no_op_count=no_op,
            unknown_count=unknown,
            error_count=error,
            validation_failed_count=validation_failed,
            recent_runs=recent_runs[:_RECENT_RUNS_IN_SIGNAL],
        )
