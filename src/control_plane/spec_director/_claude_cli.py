# src/control_plane/spec_director/_claude_cli.py
"""Thin subprocess wrapper around the claude CLI for non-interactive calls.

Uses the user's existing Claude Code OAuth session — no API key required.
"""
from __future__ import annotations

import shutil
import subprocess

_CLAUDE_BIN: str = shutil.which("claude") or "/home/dev/.local/bin/claude"
_DEFAULT_TIMEOUT = 300  # 5 minutes


def call_claude(
    user_prompt: str,
    *,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-6",
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Call the claude CLI and return the text response.

    Raises RuntimeError if the CLI exits non-zero.
    """
    cmd = [_CLAUDE_BIN, "--print", "--model", model]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    result = subprocess.run(
        cmd,
        input=user_prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: {result.stderr[:500]}"
        )
    return result.stdout.strip()
