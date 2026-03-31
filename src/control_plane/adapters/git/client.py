from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


class GitClient:
    def _run(self, args: list[str], cwd: Path | None = None) -> str:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"git command failed: {' '.join(args)}\n{proc.stderr}")
        return proc.stdout.strip()

    def clone(self, clone_url: str, workspace_path: Path) -> Path:
        repo_path = workspace_path / "repo"
        self._run(["git", "clone", clone_url, str(repo_path)])
        return repo_path

    def verify_remote_branch_exists(self, repo_path: Path, branch: str) -> None:
        out = self._run(["git", "ls-remote", "--heads", "origin", branch], cwd=repo_path)
        if not out:
            raise ValueError(f"Base branch does not exist on remote: {branch}")

    def checkout_base(self, repo_path: Path, branch: str) -> None:
        self._run(["git", "checkout", branch], cwd=repo_path)

    def create_task_branch(self, repo_path: Path, task_branch: str) -> None:
        self._run(["git", "checkout", "-b", task_branch], cwd=repo_path)

    def set_identity(self, repo_path: Path, author_name: str, author_email: str) -> None:
        self._run(["git", "config", "user.name", author_name], cwd=repo_path)
        self._run(["git", "config", "user.email", author_email], cwd=repo_path)

    def changed_files(self, repo_path: Path) -> list[str]:
        out = self._run(["git", "status", "--porcelain"], cwd=repo_path)
        files: list[str] = []
        for line in out.splitlines():
            if len(line) > 3:
                files.append(line[3:])
        return files

    def commit_all(self, repo_path: Path, message: str) -> bool:
        self._run(["git", "add", "-A"], cwd=repo_path)
        status = self._run(["git", "status", "--porcelain"], cwd=repo_path)
        if not status:
            return False
        self._run(["git", "commit", "-m", message], cwd=repo_path)
        return True

    def push_branch(self, repo_path: Path, branch: str) -> None:
        self._run(["git", "push", "-u", "origin", branch], cwd=repo_path)


def branch_allowed(base_branch: str, allowed_patterns: list[str]) -> bool:
    if not allowed_patterns:
        return True
    return any(fnmatch.fnmatch(base_branch, pattern) for pattern in allowed_patterns)
