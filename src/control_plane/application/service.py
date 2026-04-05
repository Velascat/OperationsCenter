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


class _SelfReviewVerdict:
    """Result of a kodo self-review pass."""

    __slots__ = ("verdict", "concerns")

    def __init__(self, *, verdict: str, concerns: list[str]) -> None:
        # verdict: "lgtm" | "concerns" | "error"
        self.verdict = verdict
        self.concerns = concerns


class _BaselineResult:
    """Outcome of validation run on the clean checkout before kodo executes."""

    __slots__ = ("failed", "error_text", "validation_results")

    def __init__(
        self,
        *,
        failed: bool,
        error_text: str | None,
        validation_results: list["ValidationResult"] | None = None,
    ) -> None:
        self.failed = failed
        self.error_text = error_text
        self.validation_results = validation_results


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
            validation_timeout_seconds=repo_cfg.validation_timeout_seconds,
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

            # Circuit-breaker: skip if an unresolved fix-validation task exists for the repo
            fix_task_id = self._find_open_fix_validation_task(plane_client, task.repo_key)
            if fix_task_id is not None:
                self._log_event(
                    "circuit_breaker_skip",
                    run_id,
                    fix_task_id=fix_task_id,
                    repo_key=task.repo_key,
                )
                result = ExecutionResult(
                    run_id=run_id,
                    worker_role=worker_role,
                    task_kind=task.execution_mode,
                    success=True,
                    outcome_status="skipped",
                    outcome_reason=f"open_fix_validation_task:{fix_task_id}",
                    summary=(
                        f"run_id={run_id} status=skipped reason=open_fix_validation_task "
                        f"fix_task={fix_task_id} repo={task.repo_key}"
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
                            "reason": "open_fix_validation_task",
                            "fix_task_id": fix_task_id,
                            "repo_key": task.repo_key,
                            "task_id": task.task_id,
                            "worker_role": worker_role,
                        },
                    )
                )
                artifacts.append(self.reporter.write_summary(run_dir, result))
                return result

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
            remote_existed = self.git.create_task_branch(repo_path, task_branch)
            self._log_event("task_branch_created", run_id, task_branch=task_branch, repo_path=str(repo_path), remote_existed=remote_existed)

            # If the branch existed on remote it may be behind main — merge to surface
            # any conflicts as working-tree markers so kodo can resolve them.
            merge_conflict_files: list[str] = []
            if remote_existed:
                merge_ok, merge_conflict_files = self.git.try_merge_base(repo_path, task.base_branch)
                self._log_event(
                    "merge_base",
                    run_id,
                    base=task.base_branch,
                    success=merge_ok,
                    conflict_files=merge_conflict_files,
                )

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
            goal_text = task.goal_text
            if merge_conflict_files:
                conflict_list = "\n".join(f"- `{f}`" for f in merge_conflict_files)
                goal_text = (
                    f"## Merge Conflict Resolution Required\n\n"
                    f"This branch has conflicts with `{task.base_branch}` that must be resolved "
                    f"before the original goal can be completed. Conflict markers (`<<<<<<<`, "
                    f"`=======`, `>>>>>>>`) are present in:\n\n"
                    f"{conflict_list}\n\n"
                    f"Resolve all conflict markers in those files, keeping the changes that best "
                    f"satisfy the original goal below.\n\n"
                    f"---\n\n"
                ) + goal_text
            self.kodo.write_goal_file(goal_file, goal_text, task.constraints_text)

            req = ExecutionRequest(
                run_id=run_id,
                task=task,
                repo_target=repo_target,
                workspace_path=workspace_path,
                task_branch=task_branch,
                goal_file_path=goal_file,
            )
            artifacts.append(self.reporter.write_request(run_dir, req))

            # Build run_env once — used for baseline validation and post-kodo validation
            run_env = dict(bootstrap_result.env)
            run_env.update(repo_target.env)

            phase = "baseline_validation"
            self._log_event("phase", run_id, phase=phase)
            baseline = self._run_baseline_validation(repo_target, repo_path, run_env, run_id)
            fix_validation_task_id: str | None = None
            if baseline.failed and baseline.error_text:
                fix_validation_task_id = self._maybe_create_fix_validation_task(
                    plane_client, task, baseline.error_text, run_id,
                    validation_results=baseline.validation_results,
                    repo_target=repo_target,
                )

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
                plane_client.transition_issue(task_id, "Ready for AI")
                plane_client.comment_issue(
                    task_id,
                    f"[{worker_role.capitalize()}] Execution skipped — orchestrator rate limited\n"
                    f"- run_id: {run_id}\n"
                    f"- outcome_status: skipped\n"
                    f"- outcome_reason: orchestrator_rate_limited\n"
                    "- result_status: ready_for_ai\n"
                    "- next_action: task reset to Ready for AI; will be retried on next poll",
                )
                return ExecutionResult(
                    run_id=run_id,
                    worker_role=worker_role,
                    task_kind=task.execution_mode,
                    success=False,
                    outcome_status="skipped",
                    outcome_reason="orchestrator_rate_limited",
                    summary=f"run_id={run_id} status=skipped reason=orchestrator_rate_limited",
                    artifacts=artifacts,
                    final_status="Ready for AI",
                )

            # Record execution only after kodo has actually run (not rate-limited).
            # This ensures rate-limited retries don't consume budget slots.
            self.usage_store.record_execution(role=worker_role, task_id=task.task_id, signature=signature, now=datetime.now(UTC))

            phase = "validation"
            self._log_event("phase", run_id, phase=phase)
            validation_results = self.validation.run(repo_target.validation_commands, repo_path, env=run_env, timeout_seconds=repo_target.validation_timeout_seconds)
            validation_ok = self.validation.passed(validation_results)

            validation_retried = False
            if not validation_ok:
                initial_validation_results = validation_results
                error_text = self._validation_excerpt(validation_results)
                if error_text:
                    if baseline.failed:
                        # Pre-existing failure: make fixing validation the primary goal
                        self.kodo.write_goal_file(
                            goal_file,
                            (
                                "Fix the pre-existing validation failure in this repository.\n\n"
                                "The validation suite was already broken before your changes were applied.\n"
                                "Your primary job is to make all validation commands pass.\n\n"
                                f"Original task context (secondary):\n{task.goal_text}"
                            ),
                            (
                                f"Validation error output:\n```\n{error_text}\n```\n\n"
                                + (task.constraints_text or "")
                            ),
                        )
                    else:
                        # Kodo introduced the failure: keep original goal, append as feedback
                        with open(goal_file, "a") as f:
                            f.write(f"\n\n## Validation Feedback\n\n{error_text}\n")
                kodo_result = self.kodo.run(goal_file, repo_path)
                artifacts.extend(
                    self.reporter.write_kodo(
                        run_dir,
                        self.kodo.command_to_json(kodo_result.command),
                        kodo_result.stdout,
                        kodo_result.stderr,
                        prefix="kodo_retry",
                    )
                )
                validation_results = self.validation.run(repo_target.validation_commands, repo_path, env=run_env, timeout_seconds=repo_target.validation_timeout_seconds)
                validation_ok = self.validation.passed(validation_results)
                validation_retried = True
                self._log_event("validation_retry", run_id, retry_passed=validation_ok)
                artifacts.append(
                    self.reporter.write_initial_validation(
                        run_dir,
                        [r.model_dump() for r in initial_validation_results],
                    )
                )

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

            # Policy retry: if kodo touched out-of-scope files, give it one more pass
            # with explicit constraints so it can revert or avoid those files.
            if policy_violations and task.allowed_paths:
                violated_files = ", ".join(f"`{f}`" for f in policy_violations)
                with open(goal_file, "a") as gf:
                    gf.write(
                        f"\n\n## Scope Constraint Violation\n\n"
                        f"You modified files outside the allowed scope: {violated_files}\n"
                        f"Allowed paths: {', '.join(task.allowed_paths)}\n"
                        f"Revert all changes to out-of-scope files. Keep only changes within the allowed paths.\n"
                    )
                self._log_event("policy_retry_start", run_id, violations=policy_violations)
                kodo_result = self.kodo.run(goal_file, repo_path)
                artifacts.extend(
                    self.reporter.write_kodo(
                        run_dir,
                        self.kodo.command_to_json(kodo_result.command),
                        kodo_result.stdout,
                        kodo_result.stderr,
                        prefix="kodo_policy_retry",
                    )
                )
                all_changed_files = self.git.changed_files(repo_path)
                changed_files = self._meaningful_changed_files(all_changed_files)
                internal_changed_files = [p for p in all_changed_files if p not in changed_files]
                policy_violations = self.scope_checker.find_violations(changed_files, task.allowed_paths)
                self._log_event("policy_retry_end", run_id, violations=policy_violations)

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
                        # Skip draft push if a PR review loop is already active for this
                        # task — pushing would pollute the open PR with a broken commit.
                        pr_state_file = self._PR_REVIEW_STATE_DIR / f"{task.task_id}.json"
                        if pr_state_file.exists():
                            self._log_event(
                                "draft_push_skipped",
                                run_id,
                                reason="pr_review_active",
                                task_id=task.task_id,
                            )
                        else:
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
                if outcome_status == "no_op":
                    # No material changes — task is done if validation passed, blocked if pre-existing issue remains
                    status = "Done" if validation_ok else "Blocked"
                elif policy_violations or not success:
                    status = "Blocked"
                else:
                    status = "Review"

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
                validation_retried=validation_retried,
                validation_results=validation_results,
                initial_validation_results=initial_validation_results if validation_retried else [],
                branch_pushed=branch_pushed,
                draft_branch_pushed=draft_branch_pushed,
                push_reason=push_reason,
                pull_request_url=pull_request_url,
                execution_stderr_excerpt=execution_stderr_excerpt,
                summary=summary,
                artifacts=artifacts,
                policy_violations=policy_violations,
                final_status=status,
                follow_up_task_ids=[fix_validation_task_id] if fix_validation_task_id else [],
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

    def _find_open_fix_validation_task(self, plane_client: PlaneClient, repo_key: str) -> str | None:
        """Return the id of an unresolved fix-validation task for *repo_key*, or ``None``."""
        closed_states = ("Done", "Blocked", "Cancelled")
        prefix = f"Fix pre-existing validation failure in {repo_key}"
        try:
            for issue in plane_client.list_issues():
                name = issue.get("name", "")
                state = issue.get("state", {})
                state_name = state.get("name", "") if isinstance(state, dict) else ""
                if isinstance(name, str) and name.startswith(prefix) and state_name not in closed_states:
                    return str(issue["id"])
        except Exception:  # noqa: BLE001
            return None
        return None

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

    def _run_baseline_validation(
        self,
        repo_target: "RepoTarget",
        repo_path: Path,
        run_env: dict[str, str],
        run_id: str,
    ) -> _BaselineResult:
        """Run validation on the clean checkout before kodo executes.

        A failure here means the repo was already broken — not kodo's fault.
        The result drives both the retry strategy and fix-task auto-creation.
        """
        if not repo_target.validation_commands:
            return _BaselineResult(failed=False, error_text=None)
        results = self.validation.run(repo_target.validation_commands, repo_path, env=run_env, timeout_seconds=repo_target.validation_timeout_seconds)
        if self.validation.passed(results):
            return _BaselineResult(failed=False, error_text=None, validation_results=None)
        error_text = self._validation_excerpt(results)
        self._log_event("baseline_validation_failed", run_id, error_text=error_text or "")
        return _BaselineResult(failed=True, error_text=error_text, validation_results=results)

    def _maybe_create_fix_validation_task(
        self,
        plane_client: "PlaneClient",
        task: "BoardTask",
        baseline_error_text: str,
        run_id: str,
        *,
        validation_results: list["ValidationResult"] | None = None,
        repo_target: "RepoTarget" | None = None,
    ) -> str | None:
        """Best-effort: create a dedicated fix-validation task if one isn't already open.

        Returns the new task ID on success, None on any error or duplicate.
        """
        dedup_prefix = f"Fix pre-existing validation failure in {task.repo_key}"
        try:
            occurrence_count = 0
            for issue in plane_client.list_issues():
                name = str(issue.get("name", ""))
                if not name.startswith(dedup_prefix):
                    continue
                occurrence_count += 1
                s = issue.get("state", {})
                state_name = s.get("name", "") if isinstance(s, dict) else str(s)
                if state_name not in ("Done", "Blocked", "Cancelled"):
                    self._log_event(
                        "fix_validation_task_exists",
                        run_id,
                        existing_id=str(issue.get("id", "")),
                        repo_key=task.repo_key,
                    )
                    return None

            # Include the issue we are about to create in the count.
            occurrence_count += 1

            description = self._build_fix_validation_description(
                repo_key=task.repo_key,
                baseline_error_text=baseline_error_text,
                occurrence_count=occurrence_count,
                validation_results=validation_results,
                repo_target=repo_target,
            )

            created = plane_client.create_issue(
                name=dedup_prefix,
                description=description,
                state="Backlog",
            )
            new_id = str(created.get("id", ""))
            self._log_event("fix_validation_task_created", run_id, new_id=new_id, repo_key=task.repo_key)
            return new_id or None
        except Exception as exc:
            self._log_event("fix_validation_task_error", run_id, error=str(exc), repo_key=task.repo_key)
            return None

    _MAX_OUTPUT_LINES = 50

    @classmethod
    def _build_fix_validation_description(
        cls,
        *,
        repo_key: str,
        baseline_error_text: str,
        occurrence_count: int,
        validation_results: list["ValidationResult"] | None = None,
        repo_target: "RepoTarget" | None = None,
    ) -> str:
        """Build the description body for a fix-validation issue."""
        if validation_results is None:
            # Fallback: original compact format.
            return "\n".join([
                f"repo: {repo_key}",
                "",
                "## Goal",
                f"Fix a pre-existing validation failure in the `{repo_key}` repository.",
                "The validation suite was already failing on the base branch before any changes were applied.",
                "",
                "## Validation Error",
                "```",
                baseline_error_text,
                "```",
                "",
                "## Constraints",
                "- Only modify files necessary to make the validation commands pass.",
                "- Do not introduce new failures.",
                f"- Target repo: {repo_key}",
            ])

        lines: list[str] = [
            f"repo: {repo_key}",
            "",
            "## Goal",
            f"Fix a pre-existing validation failure in the `{repo_key}` repository.",
            "The validation suite was already failing on the base branch before any changes were applied.",
            "",
            "## Baseline Failure History",
            f"This is occurrence #{occurrence_count} of baseline validation failure for this repo.",
        ]

        # Configured Validation Commands
        if repo_target and repo_target.validation_commands:
            lines.append("")
            lines.append("## Configured Validation Commands")
            for cmd in repo_target.validation_commands:
                lines.append(f"- `{cmd}`")

        # Failing Commands
        failing: list["ValidationResult"] = [r for r in validation_results if r.exit_code != 0]
        if failing:
            lines.append("")
            lines.append("## Failing Commands")
            for result in failing:
                lines.append("")
                lines.append(f"### `{result.command}` (exit code: {result.exit_code})")
                output = result.stderr.strip() if result.stderr.strip() else result.stdout.strip()
                if output:
                    truncated = "\n".join(output.splitlines()[: cls._MAX_OUTPUT_LINES])
                    lines.append("```")
                    lines.append(truncated)
                    lines.append("```")

        # Constraints
        lines.append("")
        lines.append("## Constraints")
        lines.append("- Only modify files necessary to make the validation commands pass.")
        lines.append("- Do not introduce new failures.")
        lines.append(f"- Target repo: {repo_key}")
        if failing:
            lines.append("- Fix these specific commands:")
            for result in failing:
                lines.append(f"  - `{result.command}`")

        return "\n".join(lines)

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

    def rebase_branch(
        self,
        *,
        clone_url: str,
        branch: str,
        base_branch: str,
        task_id: str,
    ) -> bool:
        """Clone repo, checkout task branch, rebase onto origin/base_branch, force-push.

        Returns True if the rebase and push succeeded.  On conflict the rebase
        is aborted so the remote branch is left unchanged.
        """
        logger = logging.getLogger(__name__)
        workspace_path = self.workspace.create()
        try:
            repo_path = self.git.clone(clone_url, workspace_path)
            self.git.set_identity(
                repo_path,
                author_name=self.settings.git.author_name,
                author_email=self.settings.git.author_email,
            )
            self.git.checkout_branch(repo_path, branch)
            success = self.git.rebase_onto_origin(repo_path, base_branch)
            if success:
                self.git.push_branch_force(repo_path, branch)
            return success
        except Exception as exc:
            logger.warning(json.dumps({"event": "rebase_branch_error", "task_id": task_id, "error": str(exc)}))
            return False
        finally:
            self.workspace.cleanup(workspace_path)

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

            # Sync with base branch; surface conflicts as markers if present
            merge_ok, conflict_files = self.git.try_merge_base(repo_path, base_branch)

            goal_file = workspace_path / "goal.md"
            combined_goal = f"{original_goal}\n\n## Review Comment\n{review_comment}"
            if not merge_ok and conflict_files:
                conflict_list = "\n".join(f"- `{f}`" for f in conflict_files)
                combined_goal = (
                    f"## Merge Conflict Resolution Required\n\n"
                    f"Conflict markers are present in:\n{conflict_list}\n\n"
                    f"Resolve them, then address the review comment below.\n\n"
                    f"---\n\n"
                ) + combined_goal
            self.kodo.write_goal_file(goal_file, combined_goal)

            repo_cfg = self.settings.repos[repo_key]
            repo_target = repo_cfg
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
            validation_results = self.validation.run(repo_target.validation_commands, repo_path, env=run_env, timeout_seconds=repo_target.validation_timeout_seconds)
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

    def run_self_review_pass(
        self,
        repo_key: str,
        clone_url: str,
        branch: str,
        base_branch: str,
        original_goal: str,
        task_id: str,
    ) -> "_SelfReviewVerdict":
        """Run kodo as a self-reviewer: read the diff vs base, emit a verdict file. Returns verdict."""
        workspace_path = self.workspace.create()
        try:
            repo_path = self.git.clone(clone_url, workspace_path)
            self.git.add_local_exclude(repo_path, ".kodo/")
            self.git.checkout_base(repo_path, branch)

            # If the branch has drifted from base, merge so the self-review sees current state
            merge_ok, conflict_files = self.git.try_merge_base(repo_path, base_branch)
            if not merge_ok:
                # Branch has unresolved conflicts — self-review cannot proceed cleanly
                conflict_list = ", ".join(conflict_files) or "unknown files"
                return _SelfReviewVerdict(
                    verdict="concerns",
                    concerns=[f"Branch has unresolved merge conflicts with `{base_branch}` in: {conflict_list}"],
                )

            goal_file = workspace_path / "goal.md"
            verdict_file = repo_path / ".review" / "verdict.txt"
            verdict_file.parent.mkdir(exist_ok=True)
            goal_text = (
                f"## Goal\n"
                f"Self-review: evaluate whether the changes on branch `{branch}` fully satisfy the original goal.\n\n"
                f"## Original Goal\n"
                f"{original_goal}\n\n"
                f"## Instructions\n"
                f"1. Run `git diff origin/{base_branch}..HEAD` to see what was changed\n"
                f"2. Evaluate whether all requirements in the Original Goal section are addressed\n"
                f"3. Write ONLY to `.review/verdict.txt` (relative to the repo root).\n"
                f"   The file MUST start with one of exactly these two tokens on the very first line, alone, with no prefix, label, or punctuation:\n"
                f"   - `LGTM` — if everything correctly satisfies the goal\n"
                f"   - `CONCERNS` — if there are specific issues; follow with one issue per line starting with `- `\n"
                f"   WRONG: 'Verdict: LGTM', 'APPROVE', '## LGTM', 'Result: CONCERNS'\n"
                f"   RIGHT: first line is the single bare word `LGTM` or `CONCERNS`, nothing else.\n"
                f"4. CRITICAL: Do NOT modify any source files, tests, or configuration. "
                f"Your only permitted output is `.review/verdict.txt`.\n"
            )
            self.kodo.write_goal_file(goal_file, goal_text)
            self.kodo.run(goal_file, repo_path)
            if not verdict_file.exists():
                return _SelfReviewVerdict(verdict="error", concerns=["Self-review did not produce .review/verdict.txt"])

            content = verdict_file.read_text().strip()
            lines = [line for line in content.splitlines() if line.strip()]
            if not lines:
                return _SelfReviewVerdict(verdict="error", concerns=[".review/verdict.txt was empty"])

            first = lines[0].strip().upper()
            _LGTM_SYNONYMS = {"LGTM", "APPROVE", "APPROVED", "LOOKS GOOD", "LOOKS GOOD TO ME"}
            if first in _LGTM_SYNONYMS:
                return _SelfReviewVerdict(verdict="lgtm", concerns=[])
            elif first == "CONCERNS":
                concerns = [line.lstrip("- ").strip() for line in lines[1:] if line.strip()]
                return _SelfReviewVerdict(verdict="concerns", concerns=concerns or ["(no details)"])
            else:
                # Fuzzy fallback: if any LGTM synonym appears anywhere without CONCERN, treat as lgtm
                self.logger.warning(
                    "Self-review verdict fuzzy fallback triggered; first line: %s",
                    first,
                )
                upper = content.upper()
                if any(s in upper for s in _LGTM_SYNONYMS) and "CONCERN" not in upper:
                    return _SelfReviewVerdict(verdict="lgtm", concerns=[])
                return _SelfReviewVerdict(verdict="concerns", concerns=[content[:500]])
        finally:
            self.workspace.cleanup(workspace_path)
