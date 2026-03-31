from __future__ import annotations

import json
import logging
import os
import re
import traceback
import uuid

from control_plane.adapters.git import GitClient, branch_allowed
from control_plane.adapters.kodo import KodoAdapter
from control_plane.adapters.plane import PlaneClient
from control_plane.adapters.reporting import Reporter
from control_plane.adapters.workspace import RepoEnvironmentBootstrapper, WorkspaceManager
from control_plane.application.scope_policy import ChangedFilePolicyChecker
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
        self.scope_checker = ChangedFilePolicyChecker()
        self.bootstrapper = RepoEnvironmentBootstrapper()
        self.logger = logging.getLogger(__name__)

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

    def run_task(self, plane_client: PlaneClient, task_id: str, *, worker_role: str = "goal") -> ExecutionResult:
        run_id = uuid.uuid4().hex[:12]
        workspace_path = self.workspace.create()
        run_dir = self.reporter.create_run_dir(task_id, run_id)
        artifacts = [self.reporter.write_request_context(run_dir, task_id, run_id, phase="initialized")]
        task: BoardTask | None = None
        phase = "initialized"
        self._log_event("run_start", run_id, task_id=task_id, workspace_path=str(workspace_path))

        try:
            phase = "fetch_task"
            self._log_event("phase", run_id, phase=phase)
            issue = plane_client.fetch_issue(task_id)
            task = plane_client.to_board_task(issue)
            repo_target = self._repo_target_for(task)
            self._log_event(
                "task_resolved",
                run_id,
                task_id=task.task_id,
                repo_key=task.repo_key,
                base_branch=task.base_branch,
                allowed_paths=task.allowed_paths,
            )

            if not branch_allowed(task.base_branch, repo_target.allowed_base_branches):
                raise ValueError(f"Base branch '{task.base_branch}' is not allowed by policy")

            phase = "running"
            self._log_event("phase", run_id, phase=phase)
            plane_client.transition_issue(task.task_id, "Running")

            phase = "repo_setup"
            self._log_event("phase", run_id, phase=phase, repo_key=repo_target.repo_key)
            repo_path = self.git.clone(repo_target.clone_url, workspace_path)
            self.git.verify_remote_branch_exists(repo_path, task.base_branch)
            self.git.checkout_base(repo_path, task.base_branch)
            self.git.set_identity(
                repo_path,
                author_name=self.settings.git.author_name,
                author_email=self.settings.git.author_email,
            )

            task_branch = self.task_branch(task)
            self.git.create_task_branch(repo_path, task_branch)
            self._log_event("task_branch_created", run_id, task_branch=task_branch, repo_path=str(repo_path))

            phase = "bootstrap"
            self._log_event("phase", run_id, phase=phase)
            repo_cfg = self.settings.repos[task.repo_key]
            bootstrap_result = self.bootstrapper.prepare(
                repo_path,
                python_binary=repo_cfg.python_binary,
                venv_dir=repo_cfg.venv_dir,
                install_dev_command=repo_cfg.install_dev_command,
                base_env=os.environ.copy(),
                enabled=repo_cfg.bootstrap_enabled,
            )
            artifacts.append(
                self.reporter.write_bootstrap(
                    run_dir,
                    [
                        {
                            "command": result.command,
                            "exit_code": result.exit_code,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                            "duration_ms": result.duration_ms,
                        }
                        for result in bootstrap_result.commands
                    ],
                )
            )

            goal_file = workspace_path / "goal.md"
            self.kodo.write_goal_file(goal_file, task.goal_text, task.constraints_text)

            req = ExecutionRequest(
                run_id=run_id,
                task=task,
                repo_target=repo_target,
                workspace_path=workspace_path,
                task_branch=task_branch,
                goal_file_path=goal_file,
            )
            artifacts.append(self.reporter.write_request(run_dir, req))

            phase = "kodo"
            self._log_event("phase", run_id, phase=phase)
            kodo_result = self.kodo.run(goal_file, repo_path)
            artifacts.extend(
                self.reporter.write_kodo(
                    run_dir,
                    self.kodo.command_to_json(kodo_result.command),
                    kodo_result.stdout,
                    kodo_result.stderr,
                )
            )

            phase = "validation"
            self._log_event("phase", run_id, phase=phase)
            run_env = dict(bootstrap_result.env)
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
            policy_violations = self.scope_checker.find_violations(changed_files, task.allowed_paths)
            if policy_violations:
                artifacts.append(self.reporter.write_policy_violation(run_dir, policy_violations))
            self._log_event(
                "policy_evaluated",
                run_id,
                changed_files=changed_files,
                policy_success=not policy_violations,
                policy_violations=policy_violations,
            )

            execution_success = kodo_result.exit_code == 0
            policy_success = not policy_violations
            success = execution_success and validation_ok and policy_success
            execution_stderr_excerpt = self._stderr_excerpt(kodo_result.stderr)

            branch_pushed = False
            draft_branch_pushed = False
            push_reason: str | None = None
            if changed_files and policy_success:
                committed = self.git.commit_all(repo_path, f"chore: apply Plane task {task.task_id}")
                if committed:
                    if success:
                        self.git.push_branch(repo_path, task_branch)
                        branch_pushed = True
                        push_reason = "success"
                    elif self.settings.git.push_on_validation_failure:
                        self.git.push_branch(repo_path, task_branch)
                        branch_pushed = True
                        draft_branch_pushed = True
                        push_reason = "draft_on_validation_failure"
            self._log_event(
                "push_evaluated",
                run_id,
                branch_pushed=branch_pushed,
                draft_branch_pushed=draft_branch_pushed,
                push_reason=push_reason or "not_pushed",
            )

            summary = (
                f"run_id={run_id} execution={'passed' if execution_success else 'failed'} "
                f"validation={'passed' if validation_ok else 'failed'} "
                f"policy={'passed' if policy_success else 'failed'} "
                f"branch_push={push_reason or 'not_pushed'} "
                f"changed_files={len(changed_files)}"
            )

            status = "Blocked" if policy_violations or not success else "Review"

            result = ExecutionResult(
                run_id=run_id,
                worker_role=worker_role,
                task_kind=task.execution_mode,
                success=success,
                changed_files=changed_files,
                validation_passed=validation_ok,
                validation_results=validation_results,
                branch_pushed=branch_pushed,
                draft_branch_pushed=draft_branch_pushed,
                push_reason=push_reason,
                pull_request_url=None,
                execution_stderr_excerpt=execution_stderr_excerpt,
                summary=summary,
                artifacts=artifacts,
                policy_violations=policy_violations,
                final_status=status,
            )
            artifacts.append(self.reporter.write_summary(run_dir, result))

            plane_client.transition_issue(task.task_id, status)
            result.final_status = status
            plane_client.comment_issue(task.task_id, self._comment_markdown(task, result, worker_role=worker_role))
            self._log_event(
                "run_end",
                run_id,
                task_id=task.task_id,
                status=status,
                success=success,
                summary=summary,
            )
            return result
        except Exception:
            artifacts.append(self.reporter.write_failure(run_dir, traceback.format_exc(), phase=phase))
            plane_client.transition_issue(task_id, "Blocked")
            plane_client.comment_issue(
                task_id,
                "\n".join(
                    [
                        f"[{worker_role.capitalize()}] Execution failed",
                        f"- run_id: {run_id}",
                        f"- worker_role: {worker_role}",
                        f"- phase: {phase}",
                        "- result_status: blocked",
                    ]
                ),
            )
            self._log_event("run_failed", run_id, task_id=task_id, phase=phase)
            raise
        finally:
            self.workspace.cleanup(workspace_path)

    def _log_event(self, event: str, run_id: str, **fields: object) -> None:
        payload = {"event": event, "run_id": run_id, **fields}
        self.logger.info(json.dumps(payload, sort_keys=True, default=str))

    @staticmethod
    def _comment_markdown(task: BoardTask, result: ExecutionResult, *, worker_role: str = "goal") -> str:
        task_kind = getattr(task, "execution_mode", result.task_kind or "goal")
        lines = [
            f"[{worker_role.capitalize()}] Execution result",
            f"- run_id: {result.run_id}",
            f"- task_id: {task.task_id}",
            f"- task_kind: {task_kind}",
            f"- worker_role: {worker_role}",
            f"- result_status: {result.final_status or ('review' if result.success else 'blocked')}",
            f"- success: {result.success}",
            f"- validation_passed: {result.validation_passed}",
            f"- policy_passed: {not result.policy_violations}",
            f"- branch_pushed: {result.branch_pushed}",
            f"- draft_branch_pushed: {result.draft_branch_pushed}",
            f"- push_reason: {result.push_reason or 'not_pushed'}",
            f"- summary: {result.summary}",
        ]
        if result.execution_stderr_excerpt:
            lines.append(f"- execution_stderr: {result.execution_stderr_excerpt}")
        if result.follow_up_task_ids:
            lines.append(f"- follow_up_task_ids: {', '.join(result.follow_up_task_ids)}")
        if result.blocked_classification:
            lines.append(f"- blocked_classification: {result.blocked_classification}")
        if result.policy_violations:
            lines.append(f"- policy_violations: {', '.join(result.policy_violations)}")
        return "\n".join(lines)

    @staticmethod
    def _stderr_excerpt(stderr: str) -> str | None:
        for line in stderr.splitlines():
            normalized = line.strip()
            if normalized:
                return normalized[:300]
        return None
