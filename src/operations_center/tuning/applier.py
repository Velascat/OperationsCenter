# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from operations_center.tuning.guardrails import AUTO_APPLY_KEYS, compute_new_value
from operations_center.tuning.models import TuningChange, TuningConfig

# Default thresholds used when no tuning config exists.
_DEFAULTS: dict[str, dict[str, int]] = {
    "observation_coverage": {"min_consecutive_runs": 2},
    "test_visibility": {"min_consecutive_runs": 3},
    "dependency_drift": {"min_consecutive_runs": 2},
}

_DEFAULT_TUNING_CONFIG_PATH = Path("config/autonomy_tuning.json")


def load_tuning_config(path: Path | None = None) -> TuningConfig | None:
    """Load tuning config from file if it exists. Returns None if absent or unreadable."""
    p = path or _DEFAULT_TUNING_CONFIG_PATH
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return TuningConfig.model_validate(data)
    except Exception:
        return None


class TuningApplier:
    """Applies bounded, audited tuning config changes.

    Only touches keys in AUTO_APPLY_KEYS for families in AUTO_APPLY_FAMILIES.
    Reads the existing tuning config (or defaults), applies the change,
    writes back, and returns an audit record.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or _DEFAULT_TUNING_CONFIG_PATH

    def current_value(self, family: str, key: str) -> int:
        """Return the current tuning value for a family/key (from file or defaults)."""
        config = load_tuning_config(self.config_path)
        if config is not None:
            val = config.overrides.get(family, {}).get(key)
            if isinstance(val, int):
                return val
        return _DEFAULTS.get(family, {}).get(key, 2)  # fallback default

    def apply(
        self,
        family: str,
        key: str,
        action: str,
        reason: str,
        generated_at: datetime,
    ) -> TuningChange | None:
        """Apply one bounded change. Returns TuningChange if applied, None if skipped."""
        if key not in AUTO_APPLY_KEYS:
            return None

        current = self.current_value(family, key)
        new_value = compute_new_value(current, action)
        if new_value is None:
            return None

        config = load_tuning_config(self.config_path) or TuningConfig(
            updated_at=generated_at, overrides={}
        )

        # Copy overrides as mutable
        overrides: dict[str, dict[str, object]] = {k: dict(v) for k, v in config.overrides.items()}
        family_overrides = dict(overrides.get(family, {}))
        family_overrides[key] = new_value
        overrides[family] = family_overrides

        updated = TuningConfig(
            version=config.version,
            updated_at=generated_at,
            overrides=overrides,
        )

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(updated.model_dump_json(indent=2))

        return TuningChange(
            family=family,
            key=key,
            before=current,
            after=new_value,
            reason=reason,
            applied_at=generated_at,
        )
