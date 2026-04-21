# src/control_plane/spec_director/_claude_cli.py
"""LLM call dispatcher for spec_director components.

When ``SWITCHBOARD_URL`` is set in the environment, all calls are routed through
SwitchBoard's OpenAI-compatible endpoint.  When it is not set, the Claude Code
CLI is used as a subprocess fallback (requires an active OAuth session).

Call sites should use ``call_claude()``; the routing is transparent to callers.
"""
from __future__ import annotations

import os
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
    """Send ``user_prompt`` to an LLM and return the text response.

    Routes through SwitchBoard when ``SWITCHBOARD_URL`` is set in the
    environment; falls back to the Claude CLI subprocess otherwise.

    Raises RuntimeError (or SwitchBoardError) on failure.
    """
    switchboard_url = os.environ.get("SWITCHBOARD_URL", "")
    if switchboard_url:
        return _call_via_switchboard(
            user_prompt, system_prompt=system_prompt, model=model, base_url=switchboard_url
        )
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


def _call_via_switchboard(
    user_prompt: str,
    *,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-6",
    base_url: str,
) -> str:
    from control_plane.adapters.switchboard.client import SwitchBoardClient

    client = SwitchBoardClient(base_url)
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return client.complete(messages, model=model)
