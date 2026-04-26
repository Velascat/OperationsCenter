"""query_artifacts() — filter indexed artifacts by stable criteria."""

from __future__ import annotations

from .models import ArtifactQuery, IndexedArtifact, ManagedArtifactIndex


def query_artifacts(
    index: ManagedArtifactIndex,
    query: ArtifactQuery | None = None,
) -> list[IndexedArtifact]:
    """Return indexed artifacts matching all filters in query.

    All filter fields are optional. An unset field (None) is skipped.
    The returned list contains only normal (non-excluded) artifact entries.
    excluded_paths are never included.

    An empty or None query returns all indexed artifacts.

    Membership filters (consumer_type, valid_for, limitation) match if
    the artifact's list field *contains* the queried value.

    Parameters
    ----------
    index:
        The ManagedArtifactIndex to search.
    query:
        Filter criteria. None or all-None fields returns all artifacts.
    """
    if query is None:
        return list(index.artifacts)

    results: list[IndexedArtifact] = []
    for artifact in index.artifacts:
        if query.repo_id is not None and artifact.repo_id != query.repo_id:
            continue
        if query.run_id is not None and artifact.run_id != query.run_id:
            continue
        if query.audit_type is not None and artifact.audit_type != query.audit_type:
            continue
        if query.artifact_kind is not None and artifact.artifact_kind != query.artifact_kind:
            continue
        if query.location is not None and artifact.location != query.location:
            continue
        if query.path_role is not None and artifact.path_role != query.path_role:
            continue
        if query.source_stage is not None and artifact.source_stage != query.source_stage:
            continue
        if query.status is not None and artifact.status != query.status:
            continue
        if query.consumer_type is not None and query.consumer_type not in artifact.consumer_types:
            continue
        if query.valid_for is not None and query.valid_for not in artifact.valid_for:
            continue
        if query.limitation is not None and query.limitation not in artifact.limitations:
            continue
        if query.content_type is not None and artifact.content_type != query.content_type:
            continue
        if query.exists_on_disk is not None and artifact.exists_on_disk != query.exists_on_disk:
            continue
        if query.is_repo_singleton is not None and artifact.is_repo_singleton != query.is_repo_singleton:
            continue
        if query.is_partial is not None and artifact.is_partial != query.is_partial:
            continue
        results.append(artifact)

    return results


__all__ = ["query_artifacts"]
