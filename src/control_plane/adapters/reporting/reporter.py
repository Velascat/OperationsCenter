from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from control_plane.domain import ExecutionRequest, ExecutionResult


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
            )
        )
        return str(path)

    def write_request(self, run_dir: Path, req: ExecutionRequest) -> str:
        path = run_dir / "request.json"
        path.write_text(req.model_dump_json(indent=2, exclude={"workspace_path", "goal_file_path"}))
        return str(path)

    def write_kodo(self, run_dir: Path, command_json: str, stdout: str, stderr: str) -> list[str]:
        cmd = run_dir / "kodo_command.json"
        out = run_dir / "kodo_stdout.log"
        err = run_dir / "kodo_stderr.log"
        cmd.write_text(command_json)
        out.write_text(stdout)
        err.write_text(stderr)
        return [str(cmd), str(out), str(err)]

    def write_validation(self, run_dir: Path, data: list[dict[str, str | int]]) -> str:
        path = run_dir / "validation.json"
        path.write_text(json.dumps(data, indent=2))
        return str(path)

    def write_policy_violation(self, run_dir: Path, violations: list[str]) -> str:
        path = run_dir / "policy_violation.json"
        path.write_text(json.dumps({"violations": violations}, indent=2))
        return str(path)

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
            )
        )
        return str(path)

    def write_summary(self, run_dir: Path, result: ExecutionResult) -> str:
        path = run_dir / "result_summary.md"
        lines = [
            "# Execution Result",
            f"- run_id: {result.run_id}",
            f"- success: {result.success}",
            f"- validation_passed: {result.validation_passed}",
            f"- branch_pushed: {result.branch_pushed}",
            f"- draft_branch_pushed: {result.draft_branch_pushed}",
            f"- push_reason: {result.push_reason}",
            f"- pull_request_url: {result.pull_request_url}",
            "",
            "## Changed Files",
        ]
        lines.extend([f"- {f}" for f in result.changed_files] or ["- (none)"])
        lines.extend(["", "## Policy Violations"])
        lines.extend([f"- {f}" for f in result.policy_violations] or ["- (none)"])
        lines.extend(["", "## Summary", result.summary])
        path.write_text("\n".join(lines))
        return str(path)
