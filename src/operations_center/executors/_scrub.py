# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Sample scrubber — strip secrets/PII before committing backend samples.

Real adapter runs capture stdout/stderr/logs/env that may contain API
tokens, absolute home paths with usernames, customer data, etc.
``scrub_sample`` is the single chokepoint every sample-write call must
pass through. CI scans committed samples for high-entropy strings and
common token prefixes as a second line of defence.

See docs/architecture/backend_control_audit.md (Phase 1 — Sample Safety).
"""
from __future__ import annotations

import os
import re
from typing import Any

# Token prefixes that are nearly always credentials.
_TOKEN_PREFIX_PATTERNS = (
    r"sk-[A-Za-z0-9_\-]{16,}",                # OpenAI / Anthropic API keys
    r"sk-ant-[A-Za-z0-9_\-]{16,}",            # Anthropic API keys
    r"ghp_[A-Za-z0-9]{16,}",                  # GitHub personal access tokens
    r"gho_[A-Za-z0-9]{16,}",                  # GitHub OAuth tokens
    r"ghs_[A-Za-z0-9]{16,}",                  # GitHub server tokens
    r"github_pat_[A-Za-z0-9_]{16,}",          # GitHub fine-grained PATs
    r"xoxb-[A-Za-z0-9\-]{16,}",               # Slack bot tokens
    r"AKIA[A-Z0-9]{16}",                      # AWS access keys
    r"AIza[A-Za-z0-9_\-]{32,}",               # Google API keys
    r"hf_[A-Za-z0-9]{16,}",                   # HuggingFace tokens
)
_TOKEN_RE = re.compile("|".join(_TOKEN_PREFIX_PATTERNS))

# Common credential-name keys to redact entirely.
_CRED_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|secret|token|password|passwd|"
    r"bearer|authorization)"
)

# Absolute paths that leak the host user.
_HOME_RE = re.compile(r"/(home|Users)/[^/\s\"']+")
_USER_RE_TEMPLATE = r"\b{user}\b"

REDACTED = "<REDACTED>"


def scrub_text(text: str) -> str:
    """Return a copy of ``text`` with secrets and home paths redacted."""
    if not text:
        return text
    out = _TOKEN_RE.sub(REDACTED, text)
    out = _HOME_RE.sub("/<USER_HOME>", out)
    real_user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if real_user and len(real_user) >= 3:
        out = re.sub(_USER_RE_TEMPLATE.format(user=re.escape(real_user)), REDACTED, out)
    return out


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return scrub_dict(value)
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    return value


def scrub_dict(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub a dict. Credential-named keys get full redaction."""
    if not isinstance(payload, dict):
        return _scrub_value(payload)
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if isinstance(k, str) and _CRED_KEY_RE.search(k):
            out[k] = REDACTED
            continue
        out[k] = _scrub_value(v)
    return out


def scrub_sample(payload: Any) -> Any:
    """Public scrubber. Use this as the single chokepoint before any sample write.

    Accepts dict / list / str / primitives. Always returns the same type
    with secrets, home paths, and credential-keyed values redacted.
    """
    if isinstance(payload, dict):
        return scrub_dict(payload)
    return _scrub_value(payload)
