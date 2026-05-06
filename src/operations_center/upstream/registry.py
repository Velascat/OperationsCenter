# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Fork registry — single source of truth for which deps are forked.

The registry IS the lockfile. Every consumer (dev, CI, prod) installs
from the same registry; the only difference is which install ``mode``
they pick.

Schema (registry.yaml):

    forks:
      <fork_id>:
        upstream:
          repo: <owner>/<repo>            # e.g. ikamensh/kodo
          latest_known_release: <ver>     # populated by poll
          latest_commit_sha: <sha>        # populated by poll
        fork:
          repo: <owner>/<repo>            # e.g. Velascat/kodo
          branch: <branch>
        base_commit: <sha>                # what fork was forked from
        fork_commit: <sha>                # the lockfile entry
        install:
          kind: cli_tool | library | binary
          modes:
            prod: <command template>
            ci:   <command template>
            dev:  <command template>
          local_clone_hint: <path>
        poll_cadence_hours: <int>
        auto_pr_push: <bool>

Install commands support template substitutions:
  {fork_commit} - the pinned SHA from this entry
  {local_clone} - resolved local clone path (dev mode only)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml


_FORK_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class RegistryError(ValueError):
    """Raised when registry.yaml is malformed or violates a rule."""


class InstallKind(str, Enum):
    CLI_TOOL = "cli_tool"
    LIBRARY  = "library"
    BINARY   = "binary"


class InstallMode(str, Enum):
    DEV  = "dev"
    CI   = "ci"
    PROD = "prod"


@dataclass(frozen=True)
class UpstreamSpec:
    repo: str
    latest_known_release: Optional[str] = None
    latest_commit_sha: Optional[str] = None


@dataclass(frozen=True)
class ForkSpec:
    repo: str
    branch: str = "main"


@dataclass(frozen=True)
class InstallSpec:
    kind: InstallKind
    modes: dict[InstallMode, str]
    local_clone_hint: Optional[str] = None

    def command_for_mode(self, mode: InstallMode) -> str:
        if mode not in self.modes:
            raise RegistryError(
                f"install mode {mode.value!r} not configured; "
                f"have: {sorted(m.value for m in self.modes)}"
            )
        return self.modes[mode]


@dataclass(frozen=True)
class ForkEntry:
    fork_id: str
    upstream: UpstreamSpec
    fork: ForkSpec
    base_commit: str
    fork_commit: str
    install: InstallSpec
    poll_cadence_hours: int = 24
    auto_pr_push: bool = False
    # When True, `operations-center-upstream auto-sync` silently applies
    # safe reconcile actions (DROP_PATCH after upstream merge, bump+
    # reinstall when zero local patches, clean rebase) without human
    # approval. Unsafe actions (rebase conflicts, auto-PR creation)
    # always abort and emit findings instead. Default true: forks
    # default-track upstream automatically.
    auto_sync: bool = True

    def render_install_command(
        self,
        mode: InstallMode,
        *,
        local_clone: Optional[Path] = None,
    ) -> str:
        cmd = self.install.command_for_mode(mode)
        substitutions = {"fork_commit": self.fork_commit}
        if local_clone is not None:
            substitutions["local_clone"] = str(local_clone)
        try:
            return cmd.format(**substitutions)
        except KeyError as exc:
            raise RegistryError(
                f"install command for {self.fork_id!r} {mode.value!r} mode "
                f"references {{{exc.args[0]}}} but no value supplied"
            ) from exc


@dataclass
class ForkRegistry:
    entries: dict[str, ForkEntry] = field(default_factory=dict)

    def get(self, fork_id: str) -> ForkEntry:
        if fork_id not in self.entries:
            raise RegistryError(f"unknown fork: {fork_id!r}")
        return self.entries[fork_id]

    def all(self) -> list[ForkEntry]:
        return [self.entries[k] for k in sorted(self.entries)]


# ── Loaders + validators ─────────────────────────────────────────────────


def _validate_repo(label: str, value: str) -> None:
    if not _REPO_RE.match(value):
        raise RegistryError(f"{label}: invalid repo {value!r} (expected 'owner/repo')")


def _validate_sha(label: str, value: str) -> None:
    if not _SHA_RE.match(value):
        raise RegistryError(f"{label}: invalid commit SHA {value!r}")


def _parse_modes(label: str, raw: Any) -> dict[InstallMode, str]:
    if not isinstance(raw, dict):
        raise RegistryError(f"{label}: install.modes must be a dict")
    out: dict[InstallMode, str] = {}
    for mode_str, cmd in raw.items():
        try:
            mode = InstallMode(mode_str)
        except ValueError as exc:
            raise RegistryError(
                f"{label}: invalid install mode {mode_str!r}; "
                f"valid: {[m.value for m in InstallMode]}"
            ) from exc
        if not isinstance(cmd, str) or not cmd.strip():
            raise RegistryError(f"{label}: install.modes.{mode_str} must be a non-empty string")
        out[mode] = cmd.strip()
    return out


def _parse_entry(fork_id: str, raw: dict) -> ForkEntry:
    if not _FORK_ID_RE.match(fork_id):
        raise RegistryError(f"invalid fork_id {fork_id!r} (lowercase alphanumeric + _ - only)")

    upstream_raw = raw.get("upstream") or {}
    if "repo" not in upstream_raw:
        raise RegistryError(f"{fork_id}: upstream.repo is required")
    _validate_repo(f"{fork_id}.upstream", upstream_raw["repo"])

    fork_raw = raw.get("fork") or {}
    if "repo" not in fork_raw:
        raise RegistryError(f"{fork_id}: fork.repo is required")
    _validate_repo(f"{fork_id}.fork", fork_raw["repo"])

    if "base_commit" not in raw or "fork_commit" not in raw:
        raise RegistryError(f"{fork_id}: base_commit and fork_commit are required")
    _validate_sha(f"{fork_id}.base_commit", str(raw["base_commit"]))
    _validate_sha(f"{fork_id}.fork_commit", str(raw["fork_commit"]))

    install_raw = raw.get("install") or {}
    if "kind" not in install_raw:
        raise RegistryError(f"{fork_id}: install.kind is required")
    try:
        install_kind = InstallKind(install_raw["kind"])
    except ValueError as exc:
        raise RegistryError(
            f"{fork_id}: invalid install.kind {install_raw['kind']!r}; "
            f"valid: {[k.value for k in InstallKind]}"
        ) from exc

    modes = _parse_modes(fork_id, install_raw.get("modes"))
    if InstallMode.DEV not in modes and InstallMode.CI not in modes and InstallMode.PROD not in modes:
        raise RegistryError(f"{fork_id}: install.modes must define at least one of dev/ci/prod")

    local_clone_hint = install_raw.get("local_clone_hint")
    if local_clone_hint is not None and not isinstance(local_clone_hint, str):
        raise RegistryError(f"{fork_id}: install.local_clone_hint must be a string")

    install = InstallSpec(
        kind=install_kind,
        modes=modes,
        local_clone_hint=local_clone_hint,
    )

    upstream = UpstreamSpec(
        repo=upstream_raw["repo"],
        latest_known_release=upstream_raw.get("latest_known_release"),
        latest_commit_sha=upstream_raw.get("latest_commit_sha"),
    )
    fork = ForkSpec(
        repo=fork_raw["repo"],
        branch=fork_raw.get("branch", "main"),
    )

    return ForkEntry(
        fork_id=fork_id,
        upstream=upstream,
        fork=fork,
        base_commit=str(raw["base_commit"]),
        fork_commit=str(raw["fork_commit"]),
        install=install,
        poll_cadence_hours=int(raw.get("poll_cadence_hours", 24)),
        auto_pr_push=bool(raw.get("auto_pr_push", False)),
        auto_sync=bool(raw.get("auto_sync", True)),
    )


# Default registry path within the package.
_DEFAULT_REGISTRY_PATH = Path(__file__).parent / "registry.yaml"


def load_registry(path: Path | None = None) -> ForkRegistry:
    """Load + validate the fork registry. Fails loudly on any malformed entry."""
    target = path or _DEFAULT_REGISTRY_PATH
    if not target.exists():
        # Empty registry is valid — no forks yet.
        return ForkRegistry()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise RegistryError(f"{target}: top level must be a mapping")
    forks = raw.get("forks") or {}
    if not isinstance(forks, dict):
        raise RegistryError(f"{target}: 'forks' key must be a mapping")
    entries: dict[str, ForkEntry] = {}
    for fork_id, entry_raw in forks.items():
        if not isinstance(entry_raw, dict):
            raise RegistryError(f"{target}: forks.{fork_id} must be a mapping")
        entries[fork_id] = _parse_entry(fork_id, entry_raw)
    return ForkRegistry(entries=entries)


# ── Local clone resolution ───────────────────────────────────────────────


def resolve_local_clone(entry: ForkEntry) -> Optional[Path]:
    """Best-effort local-clone discovery for dev-mode installs.

    Resolution order:
      1. ``OC_UPSTREAM_CLONES_ROOT`` env var + fork name
      2. ``install.local_clone_hint`` from the registry
      3. Common location patterns (~/code, ~/Documents/GitHub, ~/src)

    Returns the first existing path with the right git origin, or None.
    """
    fork_repo = entry.fork.repo  # owner/repo
    repo_name = fork_repo.split("/", 1)[-1]

    candidates: list[Path] = []

    root_env = os.environ.get("OC_UPSTREAM_CLONES_ROOT")
    if root_env:
        candidates.append(Path(root_env).expanduser() / repo_name)

    if entry.install.local_clone_hint:
        candidates.append(Path(entry.install.local_clone_hint).expanduser())

    home = Path.home()
    candidates.extend([
        home / "code" / repo_name,
        home / "Documents" / "GitHub" / repo_name,
        home / "src" / repo_name,
    ])

    for c in candidates:
        if (c / ".git").is_dir():
            return c
    return None
