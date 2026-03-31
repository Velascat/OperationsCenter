from __future__ import annotations

import os
import re
from pathlib import Path

from control_plane.adapters.git import GitClient, branch_allowed
from control_plane.adapters.kodo import KodoAdapter
from control_plane.adapters.plane import PlaneClient
from control_plane.adapters.reporting import Reporter
from control_plane.adapters.workspace import WorkspaceManager
from control_plane.application.validation import ValidationRunner
from control_plane.config import Settings
from control_plane.domain import BoardTask, ExecutionRequest, ExecutionResult, RepoTarget


class ExecutionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.git = GitClient()
        self.workspace = WorkspaceManager()
        self.kodo = KodoAdapter(settings.kodo)
        self.validation = ValidationRunner()
        self.reporter = Reporter(settings.report_root)

    @staticmethod
    def task_branch(task: BoardTask) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", task.title.lower()).strip("-")
        return f"plane/{task.task_id}-{slug}"[:100]

    def _repo_target_for(self, task: BoardTask) -> RepoTarget:
        repo_cfg = self.settings.repos[task.repo_key]
        return RepoTarget(
            repo_key=task.repo_key,
            clone_url=repo_cfg.clone_url,
            default_branch=repo_cfg.default_branch,
            workdir_name=task.repo_key,
            validation_commands=repo_cfg.validation_commands,
            env=repo_cfg.env,
            allowed_base_branches=repo_cfg.allowed_base_branches,
        )

    def run_task(self, plane_client: PlaneClient, task_id: str) -> ExecutionResult:
        issue = plane_client.fetch_issue(task_id)
        task = plane_client.to_board_task(issue)
        repo_target = self._repo_target_for(task)

        if not branch_allowed(task.base_branch, repo_target.allowed_base_branches):
            raise ValueError(f"Base branch '{task.base_branch}' is not allowed by policy")

        plane_client.transition_issue(task.task_id, "Running")

        workspace_path = self.workspace.create()
        repo_path = Path()
        try:
            repo_path = self.git.clone(repo_target.clone_url, workspace_path)
            self.git.verify_remote_branch_exists(repo_path, task.base_branch)
            self.git.checkout_base(repo_path, task.base_branch)

            task_branch = self.task_branch(task)
            self.git.create_task_branch(repo_path, task_branch)

            goal_file = workspace_path / "goal.md"
            self.kodo.write_goal_file(goal_file, task.description or task.title)

            req = ExecutionRequest(
                task=task,
                repo_target=repo_target,
                workspace_path=workspace_path,
                task_branch=task_branch,
                goal_file_path=goal_file,
            )
            run_dir = self.reporter.create_run_dir(task.task_id)
            artifacts = [self.reporter.write_request(run_dir, req)]

            kodo_result = self.kodo.run(goal_file, repo_path)
            artifacts.extend(
                self.reporter.write_kodo(
                    run_dir,
                    self.kodo.command_to_json(kodo_result.command),
                    kodo_result.stdout,
                    kodo_result.stderr,
                )
            )

            run_env = os.environ.copy()
            run_env.update(repo_target.env)
            validation_results = self.validation.run(repo_target.validation_commands, repo_path, env=run_env)
            validation_ok = self.validation.passed(validation_results)
            artifacts.append(
                self.reporter.write_validation(
                    run_dir,
                    [r.model_dump() for r in validation_results],
                )
            )

            changed_files = self.git.changed_files(repo_path)
            success = kodo_result.exit_code == 0 and validation_ok

            branch_pushed = False
            if changed_files and (success or self.settings.git.push_on_validation_failure):
                self.git.commit_all(repo_path, f"chore: apply Plane task {task.task_id}")
                self.git.push_branch(repo_path, task_branch)
                branch_pushed = True

            summary = (
                f"Kodo exit={kodo_result.exit_code}, validation={'passed' if validation_ok else 'failed'}, "
                f"changed_files={len(changed_files)}"
            )

            result = ExecutionResult(
                success=success,
                changed_files=changed_files,
                validation_passed=validation_ok,
                validation_results=validation_results,
                branch_pushed=branch_pushed,
                pull_request_url=None,
                summary=summary,
                artifacts=artifacts,
            )
            artifacts.append(self.reporter.write_summary(run_dir, result))

            status = "Review" if branch_pushed else "Blocked"
            plane_client.transition_issue(task.task_id, status)
            plane_client.comment_issue(task.task_id, self._comment_markdown(task, result))
            return result
        except Exception as exc:
            plane_client.transition_issue(task_id, "Blocked")
            plane_client.comment_issue(task_id, f"Execution failed: `{exc}`")
            raise
        finally:
            self.workspace.cleanup(workspace_path)

    @staticmethod
    def _comment_markdown(task: BoardTask, result: ExecutionResult) -> str:
        lines = [
            f"### AI execution result for {task.task_id}",
            f"- success: {result.success}",
            f"- validation_passed: {result.validation_passed}",
            f"- branch_pushed: {result.branch_pushed}",
            f"- summary: {result.summary}",
            "",
            "Artifacts:",
        ]
        lines.extend([f"- `{artifact}`" for artifact in result.artifacts])
        return "\n".join(lines)
