# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
from __future__ import annotations

import argparse

from operations_center.decision.artifact_writer import DecisionArtifactWriter
from operations_center.decision.loader import DecisionLoader
from operations_center.decision.service import DecisionEngineService, new_decision_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Decide guarded proposal candidates from retained insight artifacts")
    parser.add_argument("--insight-run-id")
    parser.add_argument("--history-limit", type=int, default=5)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--cooldown-minutes", type=int, default=120)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo")
    args = parser.parse_args()

    service = DecisionEngineService(
        loader=DecisionLoader(),
        artifact_writer=DecisionArtifactWriter(),
    )
    context = new_decision_context(
        repo_filter=args.repo,
        insight_run_id=args.insight_run_id,
        history_limit=args.history_limit,
        max_candidates=args.max_candidates,
        cooldown_minutes=args.cooldown_minutes,
        source_command="operations-center decide-proposals --dry-run" if args.dry_run else "operations-center decide-proposals",
        dry_run=args.dry_run,
    )
    artifact, artifacts = service.decide(context)
    print(f"Proposal candidates artifact written: {artifacts[0]}")
    print(f"Candidates emitted: {len(artifact.candidates)}")
    print(f"Candidates suppressed: {len(artifact.suppressed)}")


if __name__ == "__main__":
    main()
