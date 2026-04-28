"""Workspace lifecycle for the execution boundary.

The coordinator hands the adapter a workspace and a task branch, but until now
nothing populated that workspace from a real git clone, and nothing pushed the
results when the adapter finished. So every "successful" run produced changes
in a tmp directory that got cleaned up — no branch on origin, no PR, no
visible artifact for the reviewer watcher to pick up.

WorkspaceManager closes that gap:

- prepare(request): clones request.clone_url into request.workspace_path,
  checks out the base branch, and creates the task branch.
- finalize(request, result): if execution succeeded, commits any uncommitted
  changes, pushes the task branch, and (when the repo is configured for
  await_review) opens a pull request via GitHub. Returns an ExecutionResult
  updated with branch_pushed / branch_name / pull_request_url.

The manager is optional on ExecutionCoordinator — coordinator tests construct
the coordinator without one, preserving the existing pure-coordinator surface.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from operations_center.adapters.git.client import GitClient
from operations_center.contracts.execution import ExecutionRequest, ExecutionResult

logger = logging.getLogger(__name__)


# Branch prefixes whose runs should NOT be pushed or PR'd. Improve mode is
# analysis-only (output is the follow-up goal task, not code). Reviewer self-
# review writes verdict.json and exits — never code changes. If kodo wrote
# anything to the workspace it's incidental; we drop it.
_NO_PUSH_BRANCH_PREFIXES = ("improve/", "review/")

# Paths inside the workspace that must never be committed even if .gitignore
# isn't updated downstream. Belt-and-suspenders against artifact pollution.
_LOCAL_EXCLUDE_PATTERNS = (
    ".operations_center/",
    ".codex",
    ".coverage",
)


class WorkspaceManager:
    def __init__(
        self,
        *,
        git_client: GitClient | None = None,
        github_token: str | None = None,
        await_review_repos: set[str] | None = None,
        bot_identity: tuple[str, str] = ("Operations Center", "operations-center@local"),
    ) -> None:
        self._git = git_client or GitClient()
        self._token = github_token
        self._await_review = set(await_review_repos or [])
        self._bot_name, self._bot_email = bot_identity

    # ── pre-execution ────────────────────────────────────────────────────────

    def prepare(self, request: ExecutionRequest) -> None:
        """Clone the repo into workspace_path and create the task branch.

        Tolerates a pre-existing empty workspace_path (board_worker creates one).
        Fails fast if the directory exists and is non-empty.
        """
        ws = Path(request.workspace_path)
        ws.mkdir(parents=True, exist_ok=True)
        if any(ws.iterdir()):
            raise RuntimeError(
                f"workspace_path {ws} is not empty; refusing to clone into it",
            )

        # `git clone <url> .` populates the current directory directly, so the
        # repo root IS the workspace — no extra `repo/` subdir to confuse kodo.
        proc = subprocess.run(
            ["git", "clone", request.clone_url, "."],
            cwd=ws, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"git clone failed: {proc.stderr.strip() or proc.stdout.strip()}",
            )

        self._git.set_identity(ws, self._bot_name, self._bot_email)
        # Belt-and-suspenders: even if the target repo's .gitignore doesn't
        # exclude backend artifacts, this local exclude keeps them out of the
        # commit. Earlier runs without this committed kodo's own stdout.log
        # back into the repo, producing 25K-LOC garbage PRs.
        for pattern in _LOCAL_EXCLUDE_PATTERNS:
            self._git.add_local_exclude(ws, pattern)
        self._git.checkout_base(ws, request.base_branch)
        self._git.create_task_branch(ws, request.task_branch)
        logger.info(
            "WorkspaceManager.prepare: cloned %s into %s on branch %s",
            request.clone_url, ws, request.task_branch,
        )

    # ── post-execution ───────────────────────────────────────────────────────

    def finalize(
        self,
        request: ExecutionRequest,
        result: ExecutionResult,
    ) -> ExecutionResult:
        """Commit pending changes, push, and (optionally) open a PR.

        Updates and returns an ExecutionResult with branch_pushed / branch_name
        / pull_request_url populated. Failures are non-fatal — the original
        result is returned with branch_pushed=False if anything goes wrong.
        """
        if not result.success:
            return result

        # Don't push runs whose branch prefix indicates analysis-only work.
        # Improve mode: kodo analyses and the orchestrator creates a follow-up
        # goal task — that goal task is what gets shipped, not the improve
        # workspace. Reviewer self-review: writes verdict.json, never code.
        if request.task_branch.startswith(_NO_PUSH_BRANCH_PREFIXES):
            logger.info(
                "WorkspaceManager.finalize: skipping push for %s (branch prefix "
                "is analysis-only)", request.task_branch,
            )
            return result

        ws = Path(request.workspace_path)
        if not (ws / ".git").exists():
            logger.warning("WorkspaceManager.finalize: %s is not a git repo", ws)
            return result

        # Commit anything kodo left in the working tree
        if self._git.changed_files(ws):
            commit_message = self._commit_message(request)
            self._git.commit_all(ws, commit_message)

        if not self._has_new_commits(ws, request.base_branch):
            logger.info(
                "WorkspaceManager.finalize: no new commits on %s vs origin/%s — "
                "nothing to push", request.task_branch, request.base_branch,
            )
            return result

        try:
            self._git.push_branch(ws, request.task_branch)
        except Exception as exc:
            logger.warning("WorkspaceManager.finalize: push failed — %s", exc)
            return result

        pr_url = self._maybe_create_pr(request)

        return result.model_copy(update={
            "branch_pushed":    True,
            "branch_name":      request.task_branch,
            "pull_request_url": pr_url,
        })

    # ── helpers ──────────────────────────────────────────────────────────────

    def _has_new_commits(self, ws: Path, base_branch: str) -> bool:
        proc = subprocess.run(
            ["git", "rev-list", "--count", f"origin/{base_branch}..HEAD"],
            cwd=ws, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return False
        try:
            return int(proc.stdout.strip()) > 0
        except ValueError:
            return False

    def _commit_message(self, request: ExecutionRequest) -> str:
        """Derive a short commit / PR title from the request.

        Strips markdown noise (`**bold**`, `\`code\``) and `[Tag] ` prefixes
        that goal text often carries from spec-campaign builders. Falls back
        to a stable run-id slug if the goal text is empty or so prompt-shaped
        that no useful title can be extracted.
        """
        import re
        text = (request.goal_text or "").strip()
        first_line = text.splitlines()[0].strip() if text else ""
        first_line = re.sub(r"^\[\w+\]\s*", "", first_line)        # strip [Impl]/[Test]/[Improve]
        first_line = re.sub(r"\*\*([^*]+)\*\*", r"\1", first_line)  # **bold** → bold
        first_line = re.sub(r"`([^`]+)`", r"\1", first_line)        # `code` → code
        first_line = first_line.strip(" .—-")
        if not first_line:
            return f"Operations Center run {request.run_id[:8]}"
        return first_line[:72]

    def _maybe_create_pr(self, request: ExecutionRequest) -> str | None:
        if not self._token:
            return None
        if request.repo_key not in self._await_review:
            return None
        try:
            from operations_center.adapters.github_pr import GitHubPRClient
            owner, repo = GitHubPRClient.owner_repo_from_clone_url(request.clone_url)
            gh = GitHubPRClient(self._token)
            title = self._commit_message(request)
            body = (
                f"Auto-generated by Operations Center execution.\n\n"
                f"## Goal\n{request.goal_text}\n"
            )
            pr = gh.create_pr(owner, repo,
                              head=request.task_branch,
                              base=request.base_branch,
                              title=title,
                              body=body)
            return pr.get("html_url")
        except Exception as exc:
            logger.warning("WorkspaceManager: PR creation failed — %s", exc)
            return None
