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

    def _run_bytes(self, args: list[str], cwd: Path | None = None) -> bytes:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, check=False)
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
        existing = exclude_path.read_text() if exclude_path.exists() else ""
        lines = [line.strip() for line in existing.splitlines()]
        if pattern.strip() in lines:
            return
        updated = existing
        if updated and not updated.endswith("\n"):
            updated += "\n"
        updated += f"{pattern.strip()}\n"
        exclude_path.write_text(updated)

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
