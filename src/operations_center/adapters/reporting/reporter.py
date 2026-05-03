# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Reporter:
    def __init__(self, report_root: Path) -> None:
        self.report_root = report_root

    def create_run_dir(self, task_id: str, run_id: str) -> Path:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.report_root / f"{ts}_{task_id}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def write_request_context(self, run_dir: Path, task_id: str, run_id: str, phase: str) -> str:
        path = run_dir / "request_context.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "task_id": task_id,
                    "phase": phase,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ensure_ascii=False,
            )
        , encoding="utf-8")
        return str(path)

    def write_request(self, run_dir: Path, req: Any) -> str:
        path = run_dir / "request.json"
        path.write_text(req.model_dump_json(indent=2, exclude={"workspace_path", "goal_file_path"}), encoding="utf-8")
        return str(path)

    def write_plane_payload(self, run_dir: Path, payload: dict[str, object]) -> str:
        path = run_dir / "plane_work_item.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def write_kodo(self, run_dir: Path, command_json: str, stdout: str, stderr: str, *, prefix: str = "kodo") -> list[str]:
        cmd = run_dir / f"{prefix}_command.json"
        out = run_dir / f"{prefix}_stdout.log"
        err = run_dir / f"{prefix}_stderr.log"
        cmd.write_text(command_json, encoding="utf-8")
        out.write_text(stdout, encoding="utf-8")
        err.write_text(stderr, encoding="utf-8")
        return [str(cmd), str(out), str(err)]

    def write_bootstrap(self, run_dir: Path, commands: list[dict[str, object]]) -> str:
        path = run_dir / "bootstrap.json"
        path.write_text(json.dumps(commands, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def write_validation(self, run_dir: Path, data: list[dict[str, str | int]]) -> str:
        path = run_dir / "validation.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def write_initial_validation(self, run_dir: Path, data: list[dict[str, str | int]]) -> str:
        path = run_dir / "validation_initial.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def write_policy_violation(self, run_dir: Path, violations: list[str]) -> str:
        path = run_dir / "policy_violation.json"
        path.write_text(json.dumps({"violations": violations}, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def write_diff(self, run_dir: Path, *, diff_stat: str, diff_patch: str) -> list[str]:
        stat_path = run_dir / "diff_stat.txt"
        patch_path = run_dir / "diff.patch"
        stat_path.write_text(diff_stat, encoding="utf-8")
        patch_path.write_text(diff_patch, encoding="utf-8")
        return [str(stat_path), str(patch_path)]

    def write_failure(self, run_dir: Path, error: str, phase: str) -> str:
        path = run_dir / "failure.json"
        path.write_text(
            json.dumps(
                {
                    "phase": phase,
                    "error": error,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ensure_ascii=False,
            )
        , encoding="utf-8")
        return str(path)

    def write_smoke_result(
        self,
        run_dir: Path,
        *,
        task_id: str,
        fetched: bool,
        parsed: bool,
        comment_posted: bool,
        transition_state: str | None,
        restore_state: str | None,
    ) -> str:
        path = run_dir / "smoke_result.json"
        path.write_text(
            json.dumps(
                {
                    "task_id": task_id,
                    "fetched": fetched,
                    "parsed": parsed,
                    "comment_posted": comment_posted,
                    "transition_state": transition_state,
                    "restore_state": restore_state,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
                indent=2,
            ensure_ascii=False,
            )
        , encoding="utf-8")
        return str(path)

    def write_summary(self, run_dir: Path, result: Any) -> str:
        path = run_dir / "result_summary.md"
        lines = [
            "# Execution Result",
            f"- worker_role: {result.worker_role}",
            f"- task_kind: {result.task_kind}",
            f"- run_id: {result.run_id}",
            f"- success: {result.success}",
            f"- final_status: {result.final_status}",
            f"- outcome_status: {result.outcome_status}",
            f"- outcome_reason: {result.outcome_reason}",
            f"- validation_passed: {result.validation_passed}",
            f"- branch_pushed: {result.branch_pushed}",
            f"- draft_branch_pushed: {result.draft_branch_pushed}",
            f"- push_reason: {result.push_reason}",
            f"- pull_request_url: {result.pull_request_url}",
            f"- blocked_classification: {result.blocked_classification}",
            f"- follow_up_task_ids: {', '.join(result.follow_up_task_ids) if result.follow_up_task_ids else 'none'}",
            "",
            "## Changed Files",
        ]
        lines.extend([f"- {f}" for f in result.changed_files] or ["- (none)"])
        lines.extend(["", "## Internal Changed Files"])
        lines.extend([f"- {f}" for f in result.internal_changed_files] or ["- (none)"])
        if result.diff_stat_excerpt:
            lines.extend(["", "## Diff Stat"])
            lines.extend([f"- {line}" for line in result.diff_stat_excerpt.splitlines()] or ["- (none)"])
        lines.extend(["", "## Policy Violations"])
        lines.extend([f"- {f}" for f in result.policy_violations] or ["- (none)"])
        if result.validation_retried and result.initial_validation_results:
            lines.extend(["", "## Initial Validation (pre-retry)"])
            for vr in result.initial_validation_results:
                lines.append(f"- `{vr.command}`: exit_code={vr.exit_code}, duration={vr.duration_ms}ms")
                if vr.stderr.strip():
                    lines.append(f"  stderr: {vr.stderr.strip()[:200]}")
        lines.extend(["", "## Summary", result.summary])
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    def write_control_outcome(self, run_dir: Path, payload: dict[str, object]) -> str:
        path = run_dir / "control_outcome.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)
