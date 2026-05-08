# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Repo graph construction helper for OperationsCenter.

Thin wrapper around ``platform_manifest.load_effective_graph`` that
defaults to the bundled platform manifest. Optional project + local
manifest paths layer on top per the PlatformManifest design (platform
→ project → local).

OperationsCenter is a consumer of the EffectiveRepoGraph — it does not
own the manifests. Owning ResponsibilityHere is purely "give me the
merged runtime graph."
"""
from __future__ import annotations

from pathlib import Path

from platform_manifest import (
    RepoGraph,
    default_config_path,
    load_effective_graph,
)


def build_effective_repo_graph(
    *,
    project_manifest_path: Path | None = None,
    local_manifest_path: Path | None = None,
) -> RepoGraph:
    """Compose the platform manifest with optional project + local layers.

    The platform base is always the bundled ``data/platform_manifest.yaml``
    that ships with the installed ``platform-manifest`` package — OC
    never overrides this base.
    """
    return load_effective_graph(
        default_config_path(),
        project=project_manifest_path,
        local=local_manifest_path,
    )


def build_effective_repo_graph_from_settings(
    settings,
    *,
    repo_root=None,
):
    """Production entry point: build the effective graph from Settings.

    Resolution rules:
    - If ``settings.platform_manifest.enabled`` is False → returns None.
    - ``project_manifest_path``: explicit override on settings → fall back
      to ``<repo_root>/topology/project_manifest.yaml`` if it exists →
      None (platform-only).
    - ``local_manifest_path``: explicit override → if a ``project_slug`` is
      set, ask WorkStation's discovery helper → None.

    Any error (missing file referenced explicitly, malformed YAML,
    composition rule violation) is logged at WARNING and the function
    returns None. OC startup never fails because of a manifest problem.
    """
    import logging as _logging

    from platform_manifest import RepoGraphConfigError

    _logger = _logging.getLogger(__name__)

    pm = settings.platform_manifest
    if not pm.enabled:
        return None

    project_path = _resolve_project_manifest_path(pm, repo_root)
    local_path = _resolve_local_manifest_path(pm, repo_root, _logger)

    try:
        return build_effective_repo_graph(
            project_manifest_path=project_path,
            local_manifest_path=local_path,
        )
    except RepoGraphConfigError as exc:
        _logger.warning(
            "EffectiveRepoGraph construction failed (project=%s local=%s): %s; "
            "continuing without graph context",
            project_path, local_path, exc,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — defensive: never block OC startup
        _logger.warning(
            "Unexpected error building EffectiveRepoGraph: %s; "
            "continuing without graph context",
            exc,
        )
        return None


_PROJECT_MANIFEST_REL_PATH = Path("topology") / "project_manifest.yaml"


def _resolve_project_manifest_path(pm, repo_root):
    if pm.project_manifest_path is not None:
        return pm.project_manifest_path
    if repo_root is not None:
        candidate = Path(repo_root) / _PROJECT_MANIFEST_REL_PATH
        if candidate.is_file():
            return candidate
    return None


def _resolve_local_manifest_path(pm, repo_root, logger):
    if pm.local_manifest_path is not None:
        return pm.local_manifest_path
    if not pm.project_slug:
        return None
    try:
        from workstation_cli.local_manifest import discover_local_manifest  # ty:ignore[unresolved-import]
    except ImportError:
        logger.debug(
            "platform_manifest.project_slug=%r set but workstation_cli is not "
            "installed; skipping LocalManifest discovery",
            pm.project_slug,
        )
        return None
    try:
        return discover_local_manifest(pm.project_slug, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.warning(
            "WorkStation LocalManifest discovery failed for slug=%r: %s; "
            "continuing without local layer",
            pm.project_slug, exc,
        )
        return None


__all__ = [
    "build_effective_repo_graph",
    "build_effective_repo_graph_from_settings",
]
