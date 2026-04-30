# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

from pathlib import Path

from operations_center.insights.models import RepoInsightsArtifact


class InsightArtifactWriter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("tools/report/operations_center/insights")

    def write(self, artifact: RepoInsightsArtifact) -> list[str]:
        run_dir = self.root / artifact.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        json_path = run_dir / "repo_insights.json"
        json_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")

        md_path = run_dir / "repo_insights.md"
        lines = [
            "# Repo Insights",
            f"- run_id: {artifact.run_id}",
            f"- generated_at: {artifact.generated_at.isoformat()}",
            f"- repo_name: {artifact.repo.name}",
            f"- repo_path: {artifact.repo.path}",
            "",
            "## Source Snapshots",
        ]
        lines.extend([f"- {ref.run_id} @ {ref.observed_at.isoformat()}" for ref in artifact.source_snapshots] or ["- none"])
        lines.extend(["", "## Insights"])
        lines.extend(
            [
                f"- {insight.kind} | {insight.subject} | {insight.status} | {insight.dedup_key}"
                for insight in artifact.insights
            ]
            or ["- none"]
        )
        md_path.write_text("\n".join(lines), encoding="utf-8")
        return [str(json_path), str(md_path)]
