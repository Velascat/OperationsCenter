# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from pathlib import Path

from operations_center.proposer.result_models import ProposalResultsArtifact


class ProposerArtifactWriter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("tools/report/operations_center/proposer")

    def write(self, artifact: ProposalResultsArtifact) -> list[str]:
        run_dir = self.root / artifact.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        json_path = run_dir / "proposal_results.json"
        json_path.write_text(artifact.model_dump_json(indent=2))

        md_path = run_dir / "proposal_results.md"
        lines = [
            "# Proposal Results",
            f"- run_id: {artifact.run_id}",
            f"- generated_at: {artifact.generated_at.isoformat()}",
            f"- source_decision_run_id: {artifact.source_decision_run_id}",
            f"- dry_run: {str(artifact.dry_run).lower()}",
            "",
            "## Created",
        ]
        lines.extend(
            [f"- {item.family} | {item.plane_title} | {item.status} | {item.dedup_key}" for item in artifact.created]
            or ["- none"]
        )
        lines.extend(["", "## Skipped"])
        lines.extend(
            [f"- {item.family} | {item.reason} | {item.dedup_key}" for item in artifact.skipped]
            or ["- none"]
        )
        lines.extend(["", "## Failed"])
        lines.extend(
            [f"- {item.family} | {item.reason} | {item.dedup_key}" for item in artifact.failed]
            or ["- none"]
        )
        md_path.write_text("\n".join(lines))
        return [str(json_path), str(md_path)]
