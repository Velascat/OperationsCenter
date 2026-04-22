# src/control_plane/spec_director/_claude_cli.py
"""LLM call dispatcher for spec_director components.

SwitchBoard is selector-only and no longer exposes a provider-proxy API, so
spec_director calls the Claude CLI directly.
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
    """Send ``user_prompt`` to Claude CLI and return the text response."""
    return _call_claude_cli(user_prompt, system_prompt=system_prompt, model=model, timeout=timeout)


# ---------------------------------------------------------------------------
# Internal implementations
# ---------------------------------------------------------------------------


def _call_claude_cli(
    user_prompt: str,
    *,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-6",
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
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
