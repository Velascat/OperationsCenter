# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import fnmatch
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class GitClient:
    def _run(self, args: list[str], cwd: Path | None = None, *, timeout: int = 60) -> str:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"git command failed: {' '.join(args)}\n{proc.stderr}")
        return proc.stdout.strip()

    def _run_bytes(self, args: list[str], cwd: Path | None = None, *, timeout: int = 60) -> bytes:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, check=False, timeout=timeout)
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")
            raise RuntimeError(f"git command failed: {' '.join(args)}\n{stderr}")
        return proc.stdout

    def clone(self, clone_url: str, workspace_path: Path) -> Path:
        repo_path = workspace_path / "repo"
        self._run(["git", "clone", clone_url, str(repo_path)])
        return repo_path

    def add_local_exclude(self, repo_path: Path, pattern: str) -> None:
        exclude_path = repo_path / ".git" / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        lines = [line.strip() for line in existing.splitlines()]
        if pattern.strip() in lines:
            return
        updated = existing
        if updated and not updated.endswith("\n"):
            updated += "\n"
        updated += f"{pattern.strip()}\n"
        exclude_path.write_text(updated, encoding="utf-8")

    def verify_remote_branch_exists(self, repo_path: Path, branch: str) -> None:
        out = self._run(["git", "ls-remote", "--heads", "origin", branch], cwd=repo_path)
        if not out:
            raise ValueError(f"Base branch does not exist on remote: {branch}")

    def checkout_base(self, repo_path: Path, branch: str) -> None:
        self._run(["git", "checkout", branch], cwd=repo_path)
        try:
            self._run(["git", "pull", "--ff-only"], cwd=repo_path)
        except RuntimeError:
            pass  # not a fatal error — proceed with local state as-is

    def create_task_branch(self, repo_path: Path, task_branch: str) -> bool:
        """Create or checkout the task branch. Returns True if branch already existed on remote."""
        out = self._run(["git", "ls-remote", "--heads", "origin", task_branch], cwd=repo_path)
        if out:
            self._run(["git", "checkout", "-b", task_branch, f"origin/{task_branch}"], cwd=repo_path)
            return True
        self._run(["git", "checkout", "-b", task_branch], cwd=repo_path)
        return False

    def try_merge_base(self, repo_path: Path, base_branch: str) -> tuple[bool, list[str]]:
        """Merge origin/base_branch into the current branch.

        Returns (success, conflicting_files).  On conflict the merge is left
        in-progress so conflict markers are visible in the working tree — the
        caller is responsible for resolving them (e.g. via kodo) and then
        committing.
        """
        proc = subprocess.run(
            ["git", "merge", "--no-edit", f"origin/{base_branch}"],
            cwd=repo_path, capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            return True, []
        # Collect unmerged paths
        status = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        if status.returncode != 0:
            logger.warning(
                "git diff --diff-filter=U failed (rc=%d): %s",
                status.returncode,
                status.stderr.strip(),
            )
        conflict_files = [f.strip() for f in status.stdout.splitlines() if f.strip()]
        return False, conflict_files

    def recent_commits(self, repo_path: Path, max_count: int = 5) -> list[str]:
        output = self._run(
            ["git", "log", f"-n{max_count}", "--pretty=format:%h %s"],
            cwd=repo_path,
        )
        return [line.strip() for line in output.splitlines() if line.strip()]

    def recent_changed_files(self, repo_path: Path, max_count: int = 3) -> list[str]:
        output = self._run(
            ["git", "log", f"-n{max_count}", "--name-only", "--pretty=format:"],
            cwd=repo_path,
        )
        files = [line.strip() for line in output.splitlines() if line.strip()]
        normalized = [self._normalize_repo_relative_path(path) for path in files]
        deduped: list[str] = []
        for path in normalized:
            if path not in deduped:
                deduped.append(path)
        return deduped

    def set_identity(self, repo_path: Path, author_name: str, author_email: str) -> None:
        self._run(["git", "config", "user.name", author_name], cwd=repo_path)
        self._run(["git", "config", "user.email", author_email], cwd=repo_path)

    def changed_files(self, repo_path: Path) -> list[str]:
        diff_output = self._run_bytes(["git", "diff", "--name-status", "-z", "HEAD"], cwd=repo_path)
        untracked_output = self._run_bytes(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=repo_path,
        )
        files = self._parse_name_status_output(diff_output)
        files.extend(self._parse_null_delimited_paths(untracked_output))
        normalized = [self._normalize_repo_relative_path(path) for path in files]
        return sorted(set(normalized))

    def diff_stat(self, repo_path: Path) -> str:
        tracked = self._run(["git", "diff", "--stat", "HEAD"], cwd=repo_path)
        untracked = self._parse_null_delimited_paths(
            self._run_bytes(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo_path)
        )
        lines = [line for line in tracked.splitlines() if line.strip()]
        lines.extend(f" untracked | {self._normalize_repo_relative_path(path)}" for path in untracked)
        return "\n".join(lines).strip()

    def diff_patch(self, repo_path: Path) -> str:
        return self._run(["git", "diff", "--binary", "HEAD"], cwd=repo_path)

    def commit_all(self, repo_path: Path, message: str) -> bool:
        self._run(["git", "add", "-A"], cwd=repo_path)
        status = self._run(["git", "status", "--porcelain"], cwd=repo_path)
        if not status:
            return False
        self._run(["git", "commit", "-m", message], cwd=repo_path)
        return True

    def push_branch(self, repo_path: Path, branch: str) -> None:
        self._run(["git", "push", "-u", "origin", branch], cwd=repo_path)

    def push_branch_force(self, repo_path: Path, branch: str) -> None:
        """Force-push branch to origin using --force-with-lease."""
        self._run(["git", "push", "--force-with-lease", "origin", branch], cwd=repo_path)

    def checkout_branch(self, repo_path: Path, branch: str) -> None:
        """Check out an existing local or remote-tracking branch."""
        self._run(["git", "checkout", branch], cwd=repo_path)

    def revert_commit(self, repo_path: Path, commit_sha: str, *, new_branch: str) -> bool:
        """Create *new_branch* at HEAD and apply a `git revert` of *commit_sha*.

        Returns True on success. On any conflict or error, leaves the repo on
        the new branch without committing so the caller can decide what to do.
        Does NOT push the branch — callers must call push_branch() themselves.
        """
        try:
            self._run(["git", "checkout", "-b", new_branch], cwd=repo_path)
            self._run(["git", "revert", "--no-edit", commit_sha], cwd=repo_path)
            return True
        except RuntimeError:
            try:
                self._run(["git", "revert", "--abort"], cwd=repo_path)
            except RuntimeError:
                pass
            return False

    def rebase_onto_origin(self, repo_path: Path, base_branch: str) -> bool:
        """Rebase HEAD onto origin/<base_branch>.  Aborts cleanly on conflict.

        Returns True if the rebase succeeded, False if there were conflicts
        (the rebase is aborted so the working tree is left clean).
        """
        try:
            self._run(["git", "fetch", "origin", base_branch], cwd=repo_path)
        except RuntimeError:
            return False

        try:
            self._run(["git", "rebase", f"origin/{base_branch}"], cwd=repo_path)
            return True
        except RuntimeError:
            try:
                self._run(["git", "rebase", "--abort"], cwd=repo_path)
            except RuntimeError:
                pass
            return False

    def _parse_name_status_output(self, output: bytes) -> list[str]:
        if not output:
            return []

        parts = [part.decode("utf-8", errors="surrogateescape") for part in output.split(b"\x00") if part]
        files: list[str] = []
        idx = 0
        while idx < len(parts):
            status = parts[idx]
            idx += 1
            status_code = status[0]
            if status_code in {"R", "C"}:
                if idx + 1 >= len(parts):
                    break
                idx += 1
                files.append(parts[idx])
                idx += 1
                continue

            if idx >= len(parts):
                break
            files.append(parts[idx])
            idx += 1
        return files

    def _parse_null_delimited_paths(self, output: bytes) -> list[str]:
        if not output:
            return []
        return [
            part.decode("utf-8", errors="surrogateescape")
            for part in output.split(b"\x00")
            if part
        ]

    def _normalize_repo_relative_path(self, path: str) -> str:
        return str(Path(path)).replace("\\", "/").lstrip("./")


def branch_allowed(base_branch: str, allowed_patterns: list[str]) -> bool:
    if not allowed_patterns:
        return True
    return any(fnmatch.fnmatch(base_branch, pattern) for pattern in allowed_patterns)
