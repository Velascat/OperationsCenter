# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""G-V04 / G-005 — capacity-exhaustion classifier.

Some backends (notably claude-code-style assistants) print messages such as

    You're out of extra usage · resets 4:20am

into stdout and exit 0 without producing useful output. The runner sees
``exit_code == 0`` and reports success, but the run is effectively a
no-op. Audit consumers see ``status=succeeded`` with empty changed_files
— a false positive.

This module exposes a single helper that scans combined stdout/stderr
for capacity-exhaustion patterns. Real adapters call it on the success
path and, when it matches, flip their result to FAILED with
``failure_category=BACKEND_ERROR``. The pattern set is intentionally
narrow — these are observed-in-the-wild phrases, not a generic
rate-limit detector.
"""

from __future__ import annotations

import re
from typing import Optional


_CAPACITY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Claude / claude-code: "You're out of extra usage · resets 4:20am"
    re.compile(r"\bout of (?:extra )?usage\b", re.IGNORECASE),
    re.compile(r"\busage limit (?:reached|hit)\b", re.IGNORECASE),
    re.compile(r"\byou(?:'ve| have) (?:hit|reached) your (?:usage )?limit\b", re.IGNORECASE),
    re.compile(r"\bquota (?:exceeded|exhausted)\b", re.IGNORECASE),
    re.compile(r"\binsufficient quota\b", re.IGNORECASE),
    re.compile(r"\brun out of credits\b", re.IGNORECASE),
    # Stripe-style billing surface seen via some upstream APIs:
    re.compile(r"\bpayment required\b", re.IGNORECASE),
)


def classify_capacity_exhaustion(combined_output: str | None) -> Optional[str]:
    """Return a short matched-line excerpt if combined_output signals capacity exhaustion.

    ``None`` when no pattern matches. Adapters use this to decide whether
    an exit-0 run is actually a false success and should be flipped to
    FAILED with ``failure_category=BACKEND_ERROR``.
    """
    if not combined_output:
        return None
    for pattern in _CAPACITY_PATTERNS:
        match = pattern.search(combined_output)
        if match is None:
            continue
        line_start = combined_output.rfind("\n", 0, match.start()) + 1
        line_end_pos = combined_output.find("\n", match.end())
        line_end = line_end_pos if line_end_pos != -1 else len(combined_output)
        line = combined_output[line_start:line_end].strip()
        return f"capacity exhaustion detected: {line[:160]}"
    return None
