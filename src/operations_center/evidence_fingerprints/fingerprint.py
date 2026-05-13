# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Evidence hashing that ignores timestamp and cycle noise."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_IGNORED_KEYS = {
    "timestamp",
    "ts",
    "time",
    "created_at",
    "updated_at",
    "completed_at",
    "started_at",
    "cycle_id",
    "cycle",
    "run_id",
}

_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?\b|\b[0-9]{10,}\b"
)


def canonicalize_evidence(value: Any) -> Any:
    """Return a JSON-stable representation of semantic evidence."""
    if isinstance(value, Mapping):
        return {
            str(k): canonicalize_evidence(v)
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            if str(k).lower() not in _IGNORED_KEYS
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        normalized = [canonicalize_evidence(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    if isinstance(value, str):
        return _TIMESTAMP_RE.sub("<time>", value.strip())
    return value


def evidence_fingerprint(value: Any) -> str:
    payload = json.dumps(
        canonicalize_evidence(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
