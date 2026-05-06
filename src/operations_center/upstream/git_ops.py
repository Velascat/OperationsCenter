# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Git operations on local fork clones.

R3 lifecycle commands (bump/rebase/sync) call into here. All operations
are bounded subprocess calls with structured results. No git ops happen
implicitly — each command returns success/failure for the caller to
surface in the CLI.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class GitOpError(RuntimeError):
    """Raised when a git operation fails."""


@dataclass(frozen=True)
class GitResult:
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _git(repo: Path, *args: str, check: bool = False) -> GitResult:
    cmd = ["git", "-C", str(repo), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    result = GitResult(
        command=" ".join(shlex.quote(c) for c in cmd),
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if check and not result.ok:
        raise GitOpError(
            f"git command failed (exit={proc.returncode}): {result.command}\n"
            f"stderr: {proc.stderr.strip()}"
        )
    return result


def head_sha(repo: Path) -> str:
    """Return the SHA at HEAD."""
    return _git(repo, "rev-parse", "HEAD", check=True).stdout.strip()


def fetch_upstream(repo: Path, *, remote: str = "upstream") -> GitResult:
    """git fetch <remote>. Caller decides what to do with the new refs."""
    return _git(repo, "fetch", remote)


def rebase_onto(repo: Path, target_ref: str) -> GitResult:
    """git rebase <target_ref>. Returns failure (non-zero) on conflict."""
    return _git(repo, "rebase", target_ref)


def list_files_changed_between(repo: Path, base_ref: str, head_ref: str) -> list[str]:
    """git diff --name-only base..head. Empty list on no diff or git error."""
    res = _git(repo, "diff", "--name-only", f"{base_ref}..{head_ref}")
    if not res.ok:
        return []
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def remote_url(repo: Path, remote: str) -> Optional[str]:
    res = _git(repo, "remote", "get-url", remote)
    return res.stdout.strip() if res.ok else None


def is_clean(repo: Path) -> bool:
    """True iff working tree has no uncommitted changes."""
    res = _git(repo, "status", "--porcelain")
    return res.ok and not res.stdout.strip()
