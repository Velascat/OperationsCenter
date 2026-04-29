# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import json
from pathlib import Path

from operations_center.tuning.models import TuningRunArtifact

_DEFAULT_TUNING_ROOT = Path("tools/report/operations_center/tuning")


class TuningArtifactLoader:
    def __init__(self, tuning_root: Path | None = None) -> None:
        self.tuning_root = tuning_root or _DEFAULT_TUNING_ROOT

    def load_recent(self, limit: int = 10) -> list[TuningRunArtifact]:
        """Load the most recent N tuning run artifacts (for cooldown/quota checks)."""
        if not self.tuning_root.exists():
            return []
        run_dirs = sorted(
            [d for d in self.tuning_root.iterdir() if d.is_dir()], reverse=True
        )[:limit]
        results: list[TuningRunArtifact] = []
        for d in run_dirs:
            path = d / "tuning_run.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text())
                results.append(TuningRunArtifact.model_validate(data))
            except Exception:
                continue
        return results
