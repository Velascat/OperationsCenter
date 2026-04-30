# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Config schema drift detection.

Compares a deployed ``operations_center.local.yaml`` against the bundled example
config to surface keys that are missing from the operator's config.  Missing
keys almost always mean a feature added after the config was last edited is
silently disabled.

Usage::

    from operations_center.config.drift import detect_config_drift

    gaps = detect_config_drift(
        config_path="config/operations_center.local.yaml",
        example_path="config/operations_center.example.yaml",
    )
    for gap in gaps:
        logger.warning("Config drift: %s", gap)

The function is deliberately conservative — it only reports top-level and
one level of nested keys that are *entirely absent* from the deployed config.
It does not validate types or values (Pydantic does that at load time).
"""
from __future__ import annotations

from pathlib import Path

import yaml


def detect_config_drift(
    config_path: str | Path,
    example_path: str | Path,
) -> list[str]:
    """Return a list of keys present in *example_path* but absent in *config_path*.

    Each entry is a dot-separated key path, e.g. ``"escalation"`` or
    ``"escalation.webhook_url"``.  Only the first two levels are checked to
    avoid false-positives from dynamically-keyed sections like ``repos``.

    Returns an empty list when either file is missing or unparseable — callers
    should treat an empty list as "cannot determine" rather than "no drift".
    """
    try:
        config_raw = yaml.safe_load(Path(config_path).read_text()) or {}
        example_raw = yaml.safe_load(Path(example_path).read_text()) or {}
    except Exception:
        return []

    if not isinstance(config_raw, dict) or not isinstance(example_raw, dict):
        return []

    # Keys that are intentionally user-defined (dynamic keys — skip deep check)
    _DYNAMIC_TOP_LEVEL = {"repos", "scheduled_tasks"}

    gaps: list[str] = []

    for top_key, example_val in example_raw.items():
        if top_key not in config_raw:
            gaps.append(top_key)
            continue
        # Only recurse one level for non-dynamic, dict-valued keys
        if top_key in _DYNAMIC_TOP_LEVEL:
            continue
        if not isinstance(example_val, dict):
            continue
        config_sub = config_raw.get(top_key)
        if not isinstance(config_sub, dict):
            continue
        for sub_key in example_val:
            if sub_key not in config_sub:
                gaps.append(f"{top_key}.{sub_key}")

    return gaps
