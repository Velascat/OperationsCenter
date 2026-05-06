# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Fork install + verify lifecycle.

Each registered fork has three install modes (dev/ci/prod). This module
runs the right command for the chosen mode and verifies installed state
against the registry's pinned SHA.

For ``cli_tool`` kind installed via ``uv tool``, verification reads
``~/.local/share/uv/tools/<tool>/lib/python*/site-packages/*.dist-info/
direct_url.json`` to extract the actual installed git commit.
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from operations_center.upstream.registry import (
    ForkEntry,
    ForkRegistry,
    InstallKind,
    InstallMode,
    RegistryError,
    load_registry,
    resolve_local_clone,
)


class InstallError(RuntimeError):
    """Raised when an install or verify step fails."""


class VerifyStatus(str, Enum):
    OK              = "ok"
    NOT_INSTALLED   = "not_installed"
    WRONG_SHA       = "wrong_sha"
    WRONG_REPO      = "wrong_repo"
    UNKNOWN         = "unknown"


@dataclass(frozen=True)
class InstallResult:
    fork_id: str
    mode: InstallMode
    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class VerifyResult:
    fork_id: str
    status: VerifyStatus
    expected_repo: str
    expected_sha: str
    observed_repo: Optional[str]
    observed_sha: Optional[str]
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == VerifyStatus.OK


# ── Install ──────────────────────────────────────────────────────────────


def install_fork(
    entry: ForkEntry,
    mode: InstallMode,
    *,
    dry_run: bool = False,
) -> InstallResult:
    """Install one fork in the given mode. Resolves local clone if mode=dev."""
    local_clone: Optional[Path] = None
    if mode == InstallMode.DEV:
        local_clone = resolve_local_clone(entry)
        if local_clone is None:
            raise InstallError(
                f"{entry.fork_id}: dev mode requires a local clone but none found. "
                f"Set OC_UPSTREAM_CLONES_ROOT or place a clone of {entry.fork.repo} "
                f"at install.local_clone_hint."
            )
    command = entry.render_install_command(mode, local_clone=local_clone)

    if dry_run:
        return InstallResult(
            fork_id=entry.fork_id, mode=mode, command=command,
            returncode=0, stdout="<dry-run>", stderr="",
        )

    proc = subprocess.run(
        shlex.split(command), capture_output=True, text=True,
    )
    return InstallResult(
        fork_id=entry.fork_id, mode=mode, command=command,
        returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr,
    )


def install_all(
    registry: ForkRegistry,
    mode: InstallMode,
    *,
    dry_run: bool = False,
) -> list[InstallResult]:
    return [install_fork(e, mode, dry_run=dry_run) for e in registry.all()]


# ── Verify ───────────────────────────────────────────────────────────────


def _uv_tool_dir() -> Path:
    """Best-effort: read uv tool directory. Falls back to default path."""
    try:
        proc = subprocess.run(
            ["uv", "tool", "dir"],
            capture_output=True, text=True, check=True, timeout=5,
        )
        return Path(proc.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return Path.home() / ".local" / "share" / "uv" / "tools"


def _read_direct_url(tool_name: str) -> Optional[dict]:
    """Read direct_url.json from the uv-tool install of ``tool_name``.

    Returns the parsed JSON or None if the install metadata isn't present
    in the expected layout (tool not installed, or installed via a path
    other than git+).
    """
    base = _uv_tool_dir() / tool_name
    if not base.is_dir():
        return None
    site_packages_glob = list(base.glob("lib/python*/site-packages"))
    if not site_packages_glob:
        return None
    site_packages = site_packages_glob[0]
    dist_info_dirs = list(site_packages.glob(f"{tool_name.replace('-', '_')}-*.dist-info"))
    if not dist_info_dirs:
        # Try the literal package name as well
        dist_info_dirs = list(site_packages.glob(f"{tool_name}-*.dist-info"))
    if not dist_info_dirs:
        return None
    direct_url = dist_info_dirs[0] / "direct_url.json"
    if not direct_url.exists():
        return None
    try:
        return json.loads(direct_url.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _tool_name_for_fork(entry: ForkEntry) -> str:
    """Best-effort tool-name guess: assume the package is the fork repo's name."""
    return entry.fork.repo.split("/", 1)[-1]


def verify_install(entry: ForkEntry) -> VerifyResult:
    """Read installed state and compare against registry's pinned SHA.

    For ``cli_tool`` kind: reads ``direct_url.json`` from the uv-tool
    install. For ``external`` kind: reads ``git rev-parse HEAD`` from
    the local clone. ``library`` and ``binary`` kinds return UNKNOWN
    until that integration is added.
    """
    expected_repo = entry.fork.repo
    expected_sha = entry.fork_commit

    if entry.install.kind == InstallKind.EXTERNAL:
        return _verify_external(entry)

    if entry.install.kind != InstallKind.CLI_TOOL:
        return VerifyResult(
            fork_id=entry.fork_id, status=VerifyStatus.UNKNOWN,
            expected_repo=expected_repo, expected_sha=expected_sha,
            observed_repo=None, observed_sha=None,
            detail=f"verify not implemented for kind={entry.install.kind.value!r}",
        )

    tool_name = _tool_name_for_fork(entry)
    metadata = _read_direct_url(tool_name)
    if metadata is None:
        return VerifyResult(
            fork_id=entry.fork_id, status=VerifyStatus.NOT_INSTALLED,
            expected_repo=expected_repo, expected_sha=expected_sha,
            observed_repo=None, observed_sha=None,
            detail=f"{tool_name} not installed via uv tool, or direct_url.json absent",
        )

    url = metadata.get("url", "")
    vcs_info = metadata.get("vcs_info", {})
    observed_sha = vcs_info.get("commit_id") or vcs_info.get("requested_revision")

    # Best-effort observed-repo extraction from URL (handles git+ssh, git+https, file paths).
    observed_repo: Optional[str] = None
    if "github.com" in url:
        # e.g. git+ssh://git@github.com/Velascat/kodo.git@SHA
        # or git+https://github.com/Velascat/kodo.git@SHA
        # 1. take everything after 'github.com/'
        # 2. drop @SHA suffix
        # 3. drop trailing .git
        after_github = url.split("github.com/", 1)[1] if "github.com/" in url else ""
        without_sha = after_github.split("@", 1)[0]
        if without_sha.endswith(".git"):
            without_sha = without_sha[:-4]
        observed_repo = without_sha.strip("/") or None
    elif url.startswith("file://") or url.startswith("/"):
        # Local clone install — we can't easily resolve it back to a repo
        observed_repo = "<local-clone>"

    if observed_repo and "<local-clone>" not in observed_repo and observed_repo != expected_repo:
        return VerifyResult(
            fork_id=entry.fork_id, status=VerifyStatus.WRONG_REPO,
            expected_repo=expected_repo, expected_sha=expected_sha,
            observed_repo=observed_repo, observed_sha=observed_sha,
            detail=f"installed from {observed_repo!r}, expected {expected_repo!r}",
        )

    if observed_sha and not observed_sha.startswith(expected_sha) and not expected_sha.startswith(observed_sha):
        return VerifyResult(
            fork_id=entry.fork_id, status=VerifyStatus.WRONG_SHA,
            expected_repo=expected_repo, expected_sha=expected_sha,
            observed_repo=observed_repo, observed_sha=observed_sha,
            detail=f"installed sha {observed_sha} != registry pin {expected_sha}",
        )

    return VerifyResult(
        fork_id=entry.fork_id, status=VerifyStatus.OK,
        expected_repo=expected_repo, expected_sha=expected_sha,
        observed_repo=observed_repo or "<unknown>",
        observed_sha=observed_sha or "<unknown>",
    )


def _verify_external(entry: ForkEntry) -> VerifyResult:
    """Verify an external fork by reading the local clone's git HEAD."""
    expected_repo = entry.fork.repo
    expected_sha = entry.fork_commit

    clone = resolve_local_clone(entry)
    if clone is None:
        return VerifyResult(
            fork_id=entry.fork_id, status=VerifyStatus.NOT_INSTALLED,
            expected_repo=expected_repo, expected_sha=expected_sha,
            observed_repo=None, observed_sha=None,
            detail=f"no local clone resolvable for external fork {entry.fork_id}",
        )

    proc = subprocess.run(
        ["git", "-C", str(clone), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return VerifyResult(
            fork_id=entry.fork_id, status=VerifyStatus.UNKNOWN,
            expected_repo=expected_repo, expected_sha=expected_sha,
            observed_repo=str(clone), observed_sha=None,
            detail=f"git rev-parse failed: {proc.stderr.strip()[:120]}",
        )
    observed_sha = proc.stdout.strip()

    if not observed_sha.startswith(expected_sha) and not expected_sha.startswith(observed_sha):
        return VerifyResult(
            fork_id=entry.fork_id, status=VerifyStatus.WRONG_SHA,
            expected_repo=expected_repo, expected_sha=expected_sha,
            observed_repo=str(clone), observed_sha=observed_sha,
            detail=f"clone HEAD {observed_sha[:8]} != registry pin {expected_sha}",
        )

    return VerifyResult(
        fork_id=entry.fork_id, status=VerifyStatus.OK,
        expected_repo=expected_repo, expected_sha=expected_sha,
        observed_repo=str(clone), observed_sha=observed_sha,
    )


def verify_all(registry: ForkRegistry) -> list[VerifyResult]:
    return [verify_install(e) for e in registry.all()]
