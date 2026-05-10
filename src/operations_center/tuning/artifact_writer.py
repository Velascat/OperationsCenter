# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from pathlib import Path

from operations_center.tuning.models import TuningRunArtifact

_DEFAULT_TUNING_ROOT = Path("tools/report/operations_center/tuning")


class TuningArtifactWriter:
    def __init__(self, tuning_root: Path | None = None) -> None:
        self.tuning_root = tuning_root or _DEFAULT_TUNING_ROOT

    def write(self, artifact: TuningRunArtifact) -> list[str]:
        run_dir = self.tuning_root / artifact.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Primary machine-readable artifact (used by loader for cooldown/quota)
        run_path = run_dir / "tuning_run.json"
        run_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")

        # Human-readable summary split into separate files per task spec
        summary_path = run_dir / "family_tuning_summary.json"
        summary_path.write_text(
            _json_dumps(
                {
                    "run_id": artifact.run_id,
                    "generated_at": artifact.generated_at.isoformat(),
                    "window_runs": artifact.window_runs,
                    "window_start": artifact.window_start.isoformat() if artifact.window_start else None,
                    "window_end": artifact.window_end.isoformat() if artifact.window_end else None,
                    "family_metrics": [m.model_dump() for m in artifact.family_metrics],
                }
            )
        , encoding="utf-8")

        rec_path = run_dir / "tuning_recommendations.json"
        rec_path.write_text(
            _json_dumps(
                {
                    "run_id": artifact.run_id,
                    "generated_at": artifact.generated_at.isoformat(),
                    "recommendations": [r.model_dump() for r in artifact.recommendations],
                }
            )
        , encoding="utf-8")

        changes_path = run_dir / "tuning_changes.json"
        changes_path.write_text(
            _json_dumps(
                {
                    "run_id": artifact.run_id,
                    "generated_at": artifact.generated_at.isoformat(),
                    "auto_apply": artifact.auto_apply,
                    "changes_applied": [c.model_dump() for c in artifact.changes_applied],
                    "changes_skipped": [s.model_dump() for s in artifact.changes_skipped],
                }
            )
        , encoding="utf-8")

        return [str(run_path), str(summary_path), str(rec_path), str(changes_path)]


def _json_dumps(data: object) -> str:
    import json

    return json.dumps(data, indent=2, default=str, ensure_ascii=False)
