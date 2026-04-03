from __future__ import annotations

import json
import logging
import os
import re
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path

from control_plane.adapters.git import GitClient, branch_allowed
from control_plane.adapters.github_pr import GitHubPRClient
from control_plane.adapters.kodo import KodoAdapter
from control_plane.adapters.plane import PlaneClient
from control_plane.adapters.reporting import Reporter
from control_plane.adapters.workspace import RepoEnvironmentBootstrapper, WorkspaceManager
from control_plane.application.scope_policy import ChangedFilePolicyChecker
from control_plane.application.validation import ValidationRunner
from control_plane.config import Settings
from control_plane.domain import BoardTask, ExecutionRequest, ExecutionResult, RepoTarget, ValidationResult
from control_plane.execution import UsageStore


class TaskContractError(ValueError):
    """Raised when a task fails contract validation before execution begins."""


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
        self.usage_store = UsageStore(settings.execution_controls().usage_path)

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

    def run_task(
        self,
        plane_client: PlaneClient,
        task_id: str,
        *,
        worker_role: str = "goal",
        preauthorized: bool = False,
    ) -> ExecutionResult:
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
            if not task.base_branch:
                repo_cfg = self.settings.repos.get(task.repo_key)
                default_branch = repo_cfg.default_branch if repo_cfg else "main"
                task = task.model_copy(update={"base_branch": default_branch})

            phase = "contract_validation"
            self._validate_task_contract(task)

            repo_target = self._repo_target_for(task)
            self._log_event(
                "task_resolved",
                run_id,
                task_id=task.task_id,
                repo_key=task.repo_key,
                base_branch=task.base_branch,
                allowed_paths=task.allowed_paths,
            )
            signature = self.usage_store.issue_signature(issue)
            if not preauthorized:
                noop = self.usage_store.noop_decision(role=worker_role, task_id=task.task_id, signature=signature)
                if noop.should_skip:
                    self.usage_store.record_skip(
                        role=worker_role,
                        task_id=task.task_id,
                        signature=signature,
                        reason=noop.reason or "no_op",
                        detail=noop.detail,
                        now=datetime.now(UTC),
                    )
                    result = ExecutionResult(
                        run_id=run_id,
                        worker_role=worker_role,
                        task_kind=task.execution_mode,
                        success=True,
                        outcome_status="skipped",
                        outcome_reason=noop.reason or "no_op",
                        summary=f"run_id={run_id} status=skipped reason={noop.reason or 'no_op'} detail={noop.detail or 'none'}",
                        final_status=task.status,
                        artifacts=artifacts,
                    )
                    artifacts.append(
                        self.reporter.write_control_outcome(
                            run_dir,
                            {
                                "action": "execute_task",
                                "status": "skipped",
                                "reason": noop.reason or "no_op",
                                "detail": noop.detail,
                                "task_id": task.task_id,
                                "worker_role": worker_role,
                            },
                        )
                    )
                    artifacts.append(self.reporter.write_summary(run_dir, result))
                    return result

                retry = self.usage_store.retry_decision(task_id=task.task_id) if worker_role in {"goal", "test"} else None
                if retry is not None and not retry.allowed:
                    self.usage_store.record_retry_cap(
                        role=worker_role,
                        task_id=task.task_id,
                        now=datetime.now(UTC),
                        attempts=retry.attempts,
                        limit=retry.limit,
                    )
                    plane_client.transition_issue(task.task_id, "Blocked")
                    result = ExecutionResult(
                        run_id=run_id,
                        worker_role=worker_role,
                        task_kind=task.execution_mode,
                        success=False,
                        outcome_status="blocked",
                        outcome_reason="retry_cap_exceeded",
                        summary=f"run_id={run_id} status=blocked reason=retry_cap_exceeded attempts={retry.attempts} limit={retry.limit}",
                        final_status="Blocked",
                        blocked_classification="retry_cap_exceeded",
                        artifacts=artifacts,
                    )
                    artifacts.append(
                        self.reporter.write_control_outcome(
                            run_dir,
                            {
                                "action": "execute_task",
                                "status": "blocked",
                                "reason": "retry_cap_exceeded",
                                "attempts": retry.attempts,
                                "limit": retry.limit,
                                "task_id": task.task_id,
                                "worker_role": worker_role,
                            },
                        )
                    )
                    artifacts.append(self.reporter.write_summary(run_dir, result))
                    return result

                budget = self.usage_store.budget_decision(now=datetime.now(UTC))
                if not budget.allowed:
                    self.usage_store.record_skip(
                        role=worker_role,
                        task_id=task.task_id,
                        signature=signature,
                        reason=budget.reason or "budget_exceeded",
                        detail=budget.window,
                        now=datetime.now(UTC),
                        evidence={"limit": budget.limit, "current": budget.current},
                    )
                    result = ExecutionResult(
                        run_id=run_id,
                        worker_role=worker_role,
                        task_kind=task.execution_mode,
                        success=True,
                        outcome_status="skipped",
                        outcome_reason=budget.reason or "budget_exceeded",
                        summary=(
                            f"run_id={run_id} status=skipped reason={budget.reason or 'budget_exceeded'} "
                            f"window={budget.window} current={budget.current} limit={budget.limit}"
                        ),
                        final_status=task.status,
                        artifacts=artifacts,
                    )
                    artifacts.append(
                        self.reporter.write_control_outcome(
                            run_dir,
                            {
                                "action": "execute_task",
                                "status": "skipped",
                                "reason": budget.reason or "budget_exceeded",
                                "window": budget.window,
                                "limit": budget.limit,
                                "current": budget.current,
                                "task_id": task.task_id,
                                "worker_role": worker_role,
                            },
                        )
                    )
                    artifacts.append(self.reporter.write_summary(run_dir, result))
                    return result
                self.usage_store.record_execution(role=worker_role, task_id=task.task_id, signature=signature, now=datetime.now(UTC))

            if not branch_allowed(task.base_branch, repo_target.allowed_base_branches):
                raise TaskContractError(
                    f"Base branch '{task.base_branch}' is not in the allowed list for repo "
                    f"'{task.repo_key}'. Allowed: {repo_target.allowed_base_branches or ['(any)']}"
                )

            phase = "running"
            self._log_event("phase", run_id, phase=phase)
            plane_client.transition_issue(task.task_id, "Running")

            phase = "repo_setup"
            self._log_event("phase", run_id, phase=phase, repo_key=repo_target.repo_key)
            repo_path = self.git.clone(repo_target.clone_url, workspace_path)
            self.git.add_local_exclude(repo_path, ".kodo/")
            self._log_event("workspace_exclude_added", run_id, repo_path=str(repo_path), pattern=".kodo/")
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
                bootstrap_commands=repo_cfg.bootstrap_commands,
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

            if self.kodo.is_orchestrator_rate_limited(kodo_result):
                self._log_event("orchestrator_rate_limited", run_id, task_id=task_id)
                return ExecutionResult(
                    run_id=run_id,
                    worker_role=worker_role,
                    task_kind=task.execution_mode,
                    success=False,
                    outcome_status="skipped",
                    outcome_reason="orchestrator_rate_limited",
                    summary=f"run_id={run_id} status=skipped reason=orchestrator_rate_limited",
                    artifacts=artifacts,
                    final_status=task.status,
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

            all_changed_files = self.git.changed_files(repo_path)
            changed_files = self._meaningful_changed_files(all_changed_files)
            internal_changed_files = [path for path in all_changed_files if path not in changed_files]
            diff_stat = self.git.diff_stat(repo_path) if all_changed_files else ""
            diff_patch = self.git.diff_patch(repo_path) if all_changed_files else ""
            if all_changed_files:
                artifacts.extend(self.reporter.write_diff(run_dir, diff_stat=diff_stat, diff_patch=diff_patch))
            policy_violations = self.scope_checker.find_violations(changed_files, task.allowed_paths)
            if policy_violations:
                artifacts.append(self.reporter.write_policy_violation(run_dir, policy_violations))
            self._log_event(
                "policy_evaluated",
                run_id,
                changed_files=changed_files,
                internal_changed_files=internal_changed_files,
                policy_success=not policy_violations,
                policy_violations=policy_violations,
            )

            execution_success = kodo_result.exit_code == 0
            policy_success = not policy_violations
            success = execution_success and validation_ok and policy_success
            push_reason: str | None = None
            outcome_status = "executed"
            outcome_reason = None
            if execution_success and not changed_files:
                success = True
                outcome_status = "no_op"
                outcome_reason = "internal_only_change" if internal_changed_files else "no_material_change"
                summary = (
                    f"run_id={run_id} execution=passed validation={'passed' if validation_ok else 'failed'} "
                    f"policy={'passed' if policy_success else 'failed'} branch_push=not_pushed "
                    f"changed_files=0 internal_changed_files={len(internal_changed_files)} no_op=true"
                )
            else:
                summary = (
                    f"run_id={run_id} execution={'passed' if execution_success else 'failed'} "
                    f"validation={'passed' if validation_ok else 'failed'} "
                    f"policy={'passed' if policy_success else 'failed'} "
                    f"branch_push={push_reason or 'not_pushed'} "
                    f"changed_files={len(changed_files)}"
                )
            execution_stderr_excerpt = self._stderr_excerpt(kodo_result.stderr)

            branch_pushed = False
            draft_branch_pushed = False
            pull_request_url: str | None = None
            status: str | None = None
            if changed_files and policy_success:
                committed = self.git.commit_all(repo_path, f"chore: apply Plane task {task.task_id}")
                if committed:
                    if success:
                        self.git.push_branch(repo_path, task_branch)
                        branch_pushed = True
                        push_reason = "success"
                        repo_cfg = self.settings.repos.get(task.repo_key)
                        if repo_cfg and repo_cfg.await_review:
                            token = self.settings.repo_git_token(task.repo_key)
                            controls = self.settings.execution_controls()
                            if controls.pr_dry_run:
                                self._log_event(
                                    "pr_dry_run",
                                    run_id,
                                    task_id=task.task_id,
                                    head=task_branch,
                                    base=task.base_branch,
                                    reason="CONTROL_PLANE_PR_DRY_RUN=1",
                                )
                            elif token:
                                try:
                                    owner, repo_name = GitHubPRClient.owner_repo_from_clone_url(repo_cfg.clone_url)
                                    pr_client = GitHubPRClient(token)
                                    pr = pr_client.create_pr(
                                        owner,
                                        repo_name,
                                        head=task_branch,
                                        base=task.base_branch,
                                        title=task.title,
                                        body=f"Automated by Control Plane — task `{task.task_id}`",
                                    )
                                    pull_request_url = pr["html_url"]
                                    self._write_pr_review_state(task, task_branch, owner, repo_name, pr)
                                    plane_client.transition_issue(task.task_id, "In Review")
                                    status = "In Review"
                                    self._log_event(
                                        "pr_review_pending",
                                        run_id,
                                        pr_url=pull_request_url,
                                        pr_number=pr["number"],
                                        head=task_branch,
                                        base=task.base_branch,
                                        repo=f"{owner}/{repo_name}",
                                    )
                                except Exception as exc:
                                    self._log_event("pr_create_failed", run_id, error=str(exc))
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
                pull_request_url=pull_request_url,
            )

            if status is None:
                status = "Blocked" if outcome_status == "no_op" or policy_violations or not success else "Review"

            result = ExecutionResult(
                run_id=run_id,
                worker_role=worker_role,
                task_kind=task.execution_mode,
                success=success,
                outcome_status=outcome_status,
                outcome_reason=outcome_reason,
                changed_files=changed_files,
                internal_changed_files=internal_changed_files,
                diff_stat_excerpt=self._diff_stat_excerpt(diff_stat),
                validation_passed=validation_ok,
                validation_results=validation_results,
                branch_pushed=branch_pushed,
                draft_branch_pushed=draft_branch_pushed,
                push_reason=push_reason,
                pull_request_url=pull_request_url,
                execution_stderr_excerpt=execution_stderr_excerpt,
                summary=summary,
                artifacts=artifacts,
                policy_violations=policy_violations,
                final_status=status,
            )
            artifacts.append(self.reporter.write_summary(run_dir, result))
            artifacts.append(
                self.reporter.write_control_outcome(
                    run_dir,
                    {
                        "action": "execute_task",
                        "status": result.outcome_status,
                        "reason": result.outcome_reason,
                        "task_id": task.task_id,
                        "worker_role": worker_role,
                    },
                )
            )

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
            f"- outcome_status: {result.outcome_status}",
            f"- outcome_reason: {result.outcome_reason or 'none'}",
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
        if result.changed_files:
            display = ", ".join(result.changed_files[:5])
            if len(result.changed_files) > 5:
                display = f"{display}, ... (+{len(result.changed_files) - 5} more)"
            lines.append(f"- changed_files: {display}")
        if result.internal_changed_files:
            display = ", ".join(result.internal_changed_files[:5])
            if len(result.internal_changed_files) > 5:
                display = f"{display}, ... (+{len(result.internal_changed_files) - 5} more)"
            lines.append(f"- internal_changed_files: {display}")
        if result.diff_stat_excerpt:
            lines.append(f"- diff_stat: {result.diff_stat_excerpt.splitlines()[0]}")
        if result.follow_up_task_ids:
            lines.append(f"- follow_up_task_ids: {', '.join(result.follow_up_task_ids)}")
        if result.blocked_classification:
            lines.append(f"- blocked_classification: {result.blocked_classification}")
        if result.validation_passed is False and result.validation_results is not None:
            excerpt = ExecutionService._validation_excerpt(result.validation_results)
            if excerpt is not None:
                lines.append(f"- validation_errors:\n```\n{excerpt}\n```")
        if result.policy_violations:
            lines.append(f"- policy_violations: {', '.join(result.policy_violations)}")
        return "\n".join(lines)

    @staticmethod
    def _validation_excerpt(validation_results: list[ValidationResult], max_lines: int = 20) -> str | None:
        failed = [r for r in validation_results if r.exit_code != 0]
        if not failed:
            return None
        output_lines: list[str] = []
        for r in failed:
            output_lines.append(f"[{r.command}]")
            text = r.stderr.strip() or r.stdout.strip()
            if text:
                output_lines.extend(text.splitlines())
        if len(output_lines) > max_lines:
            output_lines = output_lines[-max_lines:]
        return "\n".join(output_lines) or None

    @staticmethod
    def _stderr_excerpt(stderr: str) -> str | None:
        for line in stderr.splitlines():
            normalized = line.strip()
            if normalized:
                return normalized[:300]
        return None

    @staticmethod
    def _diff_stat_excerpt(diff_stat: str) -> str | None:
        lines = [line.strip() for line in diff_stat.splitlines() if line.strip()]
        if not lines:
            return None
        return "\n".join(lines[:4])

    @staticmethod
    def _is_internal_execution_path(path: str) -> bool:
        normalized = path.strip().replace("\\", "/").lower()
        return normalized.startswith("kodo/")

    @classmethod
    def _meaningful_changed_files(cls, changed_files: list[str]) -> list[str]:
        return [path for path in changed_files if not cls._is_internal_execution_path(path)]

    def _validate_task_contract(self, task: "BoardTask") -> None:
        """Fail early and clearly when task metadata violates known contracts."""
        if not task.repo_key:
            raise TaskContractError(
                "Task has no repo key. Add a 'repo: <key>' label matching a configured repo."
            )
        if task.repo_key not in self.settings.repos:
            known = sorted(self.settings.repos.keys())
            raise TaskContractError(
                f"Unknown repo key '{task.repo_key}'. "
                f"Add a 'repo: <key>' label with one of the configured repos: {known}"
            )
        repo_cfg = self.settings.repos[task.repo_key]
        if not repo_cfg.clone_url:
            raise TaskContractError(
                f"Repo '{task.repo_key}' has no clone_url configured."
            )
        if not task.goal_text or not task.goal_text.strip():
            raise TaskContractError(
                "Task has no goal text. Add a '## Goal' section or write the goal as plain description text."
            )

    _PR_REVIEW_STATE_DIR = Path("state/pr_reviews")

    def _write_pr_review_state(
        self,
        task: "BoardTask",
        branch: str,
        owner: str,
        repo_name: str,
        pr: dict,
    ) -> None:
        state_dir = self._PR_REVIEW_STATE_DIR
        state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "owner": owner,
            "repo": repo_name,
            "repo_key": task.repo_key,
            "pr_number": pr["number"],
            "pr_url": pr["html_url"],
            "task_id": task.task_id,
            "branch": branch,
            "base": task.base_branch,
            "original_goal": task.goal_text,
            "created_at": datetime.now(UTC).isoformat(),
            "loop_count": 0,
            "last_bot_comment_id": None,
            "bot_comment_ids": [],
            "processed_human_comment_ids": [],
        }
        (state_dir / f"{task.task_id}.json").write_text(json.dumps(state, indent=2))

    def run_review_pass(
        self,
        repo_key: str,
        clone_url: str,
        branch: str,
        base_branch: str,
        original_goal: str,
        review_comment: str,
        task_id: str,
    ) -> tuple[bool, list[str]]:
        """Run kodo on an existing branch to address a review comment. Returns (success, changed_files)."""
        workspace_path = self.workspace.create()
        try:
            repo_path = self.git.clone(clone_url, workspace_path)
            self.git.add_local_exclude(repo_path, ".kodo/")
            self.git.checkout_base(repo_path, branch)
            self.git.set_identity(
                repo_path,
                author_name=self.settings.git.author_name,
                author_email=self.settings.git.author_email,
            )

            goal_file = workspace_path / "goal.md"
            combined_goal = f"{original_goal}\n\n## Review Comment\n{review_comment}"
            self.kodo.write_goal_file(goal_file, combined_goal)

            repo_cfg = self.settings.repos[repo_key]
            bootstrap_result = self.bootstrapper.prepare(
                repo_path,
                python_binary=repo_cfg.python_binary,
                venv_dir=repo_cfg.venv_dir,
                install_dev_command=repo_cfg.install_dev_command,
                base_env=os.environ.copy(),
                enabled=repo_cfg.bootstrap_enabled,
                bootstrap_commands=repo_cfg.bootstrap_commands,
            )

            kodo_result = self.kodo.run(goal_file, repo_path)

            if self.kodo.is_orchestrator_rate_limited(kodo_result):
                return False, []

            run_env = dict(bootstrap_result.env)
            run_env.update(repo_cfg.env)
            validation_results = self.validation.run(repo_cfg.validation_commands, repo_path, env=run_env)
            validation_ok = self.validation.passed(validation_results)

            all_changed = self.git.changed_files(repo_path)
            changed_files = self._meaningful_changed_files(all_changed)

            if kodo_result.exit_code == 0 and validation_ok and changed_files:
                committed = self.git.commit_all(repo_path, f"chore: apply review revision for {task_id}")
                if committed:
                    self.git.push_branch(repo_path, branch)
                    return True, changed_files

            return False, changed_files
        finally:
            self.workspace.cleanup(workspace_path)
