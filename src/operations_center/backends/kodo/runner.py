# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Kodo binary helpers.

After Phase 3 of the runtime extraction, this module no longer runs
the kodo binary itself — that's ExecutorRuntime's job. What stays
here is the kodo-specific knowledge the OC backend needs:

  - ``KodoAdapter.write_goal_file`` — assembles the goal markdown
  - ``KodoAdapter.build_command`` — composes the kodo CLI argv
  - ``KodoAdapter.get_version`` — reads ``kodo --version``

The actual subprocess invocation is performed by
``executor_runtime.ExecutorRuntime`` against the RxP RuntimeInvocation
that the kodo backend's invoker constructs.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from operations_center.config.settings import KodoSettings


class KodoAdapter:
    def __init__(self, settings: KodoSettings) -> None:
        self.settings = settings

    def write_goal_file(self, path: Path, goal_text: str, constraints_text: str | None = None) -> Path:
        lines = ["## Goal", goal_text.strip()]
        if constraints_text:
            lines.extend(["", "## Constraints", constraints_text.strip()])
        lines.extend([
            "",
            "## Commit message",
            "Write a descriptive conventional-commit message explaining WHAT changed and WHY.",
            "Format: `<type>(<scope>): <short summary>`",
            "Follow with a blank line and 1-3 sentences of body context (motivation, approach, trade-offs).",
            "Do NOT use generic messages like 'apply task' or include task IDs in the subject line.",
        ])
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        return path

    def build_command(
        self,
        goal_file: Path,
        repo_path: Path,
        profile: "KodoSettings | None" = None,
        kodo_mode: str = "goal",
    ) -> list[str]:
        """Return the Kodo CLI command list.

        *profile* overrides individual fields from ``self.settings``.  Only
        fields present on a ``KodoSettings`` instance are considered; the
        binary is always taken from ``self.settings``.  *kodo_mode* selects
        the kodo invocation mode: "goal" (default), "test", or "improve".
        """
        s = self.settings
        # --goal-file, --test, and --improve are mutually exclusive in kodo's
        # argparse (same group).  Build the shared tail without any mode flag,
        # then prepend the correct mode flag per kodo_mode.
        tail = [
            "--project",
            str(repo_path),
            "--team",
            (profile.team if profile else s.team),
            "--cycles",
            str(profile.cycles if profile else s.cycles),
            "--exchanges",
            str(profile.exchanges if profile else s.exchanges),
            "--orchestrator",
            (profile.orchestrator if profile else s.orchestrator),
            "--effort",
            (profile.effort if profile else s.effort),
            "--yes",
        ]
        if kodo_mode == "test":
            return [s.binary, "--test"] + tail
        if kodo_mode == "improve":
            return [s.binary, "--improve"] + tail
        return [s.binary, "--goal-file", str(goal_file)] + tail

    @staticmethod
    def get_version(binary: str) -> str | None:
        """Return the kodo binary version string, or None on failure.

        The result is intentionally not cached at the module level so that
        version-change detection (S6-8) works correctly across the lifetime of
        a long-running watcher process.  Callers should cache it themselves if
        they need to avoid repeated subprocess calls.
        """
        try:
            result = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = (result.stdout.strip() or result.stderr.strip())[:64]
            return version or None
        except Exception:
            return None


# ── module-level helpers (cited in autonomy_gaps.md) ─────────────────────────

def _get_kodo_version(binary: str | None = None) -> str | None:
    """Return the kodo binary version string, or None on failure.

    Module-level shim around ``KodoAdapter.get_version`` so callers outside
    an adapter instance (e.g. capture writers, observability collectors) can
    record the version that produced an execution. No side effects.

    See `docs/design/autonomy_gaps.md` S6-8 (Kodo Version Attribution).
    """
    if binary is None:
        binary = "kodo"
    return KodoAdapter.get_version(binary)
