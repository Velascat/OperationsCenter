# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

from pathlib import Path

from operations_center.decision.models import ProposalCandidatesArtifact


class DecisionArtifactWriter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("tools/report/operations_center/decision")

    def write(self, artifact: ProposalCandidatesArtifact) -> list[str]:
        run_dir = self.root / artifact.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        json_path = run_dir / "proposal_candidates.json"
        json_path.write_text(artifact.model_dump_json(indent=2))

        md_path = run_dir / "proposal_candidates.md"
        lines = [
            "# Proposal Candidates",
            f"- run_id: {artifact.run_id}",
            f"- generated_at: {artifact.generated_at.isoformat()}",
            f"- source_insight_run_id: {artifact.source_insight_run_id}",
            "",
            "## Candidates",
        ]
        lines.extend(
            [
                f"- {candidate.family} | {candidate.subject} | {candidate.dedup_key}"
                for candidate in artifact.candidates
            ]
            or ["- none"]
        )
        lines.extend(["", "## Suppressed"])
        lines.extend(
            [
                f"- {item.family} | {item.subject} | {item.reason} | {item.dedup_key}"
                for item in artifact.suppressed
            ]
            or ["- none"]
        )
        md_path.write_text("\n".join(lines))
        return [str(json_path), str(md_path)]
