from __future__ import annotations

import argparse

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings
from operations_center.proposer import CandidateProposerIntegrationService
from operations_center.proposer.candidate_integration import new_proposer_integration_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Create Plane tasks from retained proposal candidates")
    parser.add_argument("--config", required=True)
    parser.add_argument("--decision-run-id")
    parser.add_argument("--max-create", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repo")
    args = parser.parse_args()

    settings = load_settings(args.config)
    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )
    try:
        service = CandidateProposerIntegrationService(settings=settings, client=client)
        context = new_proposer_integration_context(
            repo_filter=args.repo,
            decision_run_id=args.decision_run_id,
            max_create=args.max_create,
            dry_run=args.dry_run,
            source_command="operations-center propose-from-candidates",
        )
        artifact, artifacts = service.run(context)
        print(f"Proposal results artifact written: {artifacts[0]}")
        print(f"Created: {len(artifact.created)}")
        print(f"Skipped: {len(artifact.skipped)}")
        print(f"Failed: {len(artifact.failed)}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
