# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Repo graph construction helper for OperationsCenter.

Thin wrapper around ``platform_manifest.load_effective_graph`` that
defaults to the bundled platform manifest. Optional private, project,
work-scope, and local manifest paths layer on top per the PlatformManifest
design (platform -> private -> project/work-scope -> local).

OperationsCenter is a consumer of the EffectiveRepoGraph — it does not
own the manifests. Owning ResponsibilityHere is purely "give me the
merged runtime graph."
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

from platform_manifest import (
    RepoGraph,
    default_config_path,
    load_effective_graph,
)


def build_effective_repo_graph(
    *,
    private_manifest_path: Path | None = None,
    project_manifest_path: Path | None = None,
    work_scope_manifest_path: Path | None = None,
    local_manifest_path: Path | None = None,
) -> RepoGraph:
    """Compose the platform manifest with optional private/project/work-scope/local layers.

    Exactly one of ``project_manifest_path`` and ``work_scope_manifest_path``
    may be set (mutually exclusive — single-project vs work-scope mode).
    The platform base is always the bundled ``data/platform_manifest.yaml``
    that ships with the installed ``platform-manifest`` package — OC
    never overrides this base.
    """
    return _load_effective_graph_compatible(
        default_config_path(),
        private=private_manifest_path,
        project=project_manifest_path,
        work_scope=work_scope_manifest_path,
        local=local_manifest_path,
    )


def _load_effective_graph_compatible(
    base: Path,
    *,
    private: Path | None = None,
    project: Path | None = None,
    work_scope: Path | None = None,
    local: Path | None = None,
):
    params = inspect.signature(load_effective_graph).parameters
    if "private" in params:
        return load_effective_graph(
            base,
            private=private,
            project=project,
            work_scope=work_scope,
            local=local,
        )
    if private is None:
        return load_effective_graph(
            base,
            project=project,
            work_scope=work_scope,
            local=local,
        )
    local_impl = _load_local_platform_manifest_impl()
    return local_impl(
        base,
        private=private,
        project=project,
        work_scope=work_scope,
        local=local,
    )


def _load_local_platform_manifest_impl():
    workspace_root = Path(__file__).resolve().parents[3]
    package_init = workspace_root / "PlatformManifest" / "src" / "platform_manifest" / "__init__.py"
    if not package_init.is_file():
        raise RuntimeError(
            "private manifest layering requested, but installed platform_manifest "
            "package does not support it and sibling PlatformManifest source was not found"
        )
    package_dir = str(package_init.parent.parent)
    if package_dir not in sys.path:
        sys.path.insert(0, package_dir)
    spec = importlib.util.spec_from_file_location(
        "platform_manifest_local_fallback",
        package_init,
        submodule_search_locations=[str(package_init.parent)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"could not load sibling PlatformManifest source from {package_init}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.load_effective_graph


def build_effective_repo_graph_from_settings(
    settings,
    *,
    repo_root=None,
):
    """Production entry point: build the effective graph from Settings.

    Resolution rules:
    - If ``settings.platform_manifest.enabled`` is False → returns None.
    - ``private_manifest_path``: explicit override on settings → fall back
      to a sibling private topology repository that exposes
      ``manifests/<project_slug>/private_manifest.yaml`` when present locally
      → None.
    - ``project_manifest_path``: explicit override on settings → fall back
      to ``<repo_root>/topology/project_manifest.yaml`` if it exists →
      None (platform-only).
    - ``local_manifest_path``: explicit override → if a ``project_slug`` is
      set, ask PlatformDeployment/PlatformDeployment's discovery helper -> None.

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

    work_scope_path = pm.work_scope_manifest_path
    private_path = _resolve_private_manifest_path(pm, repo_root)
    # Project path is only resolved (with topology/ fallback) when
    # work-scope mode is not selected. The two are mutually exclusive
    # at the settings layer.
    project_path = (
        None if work_scope_path is not None
        else _resolve_project_manifest_path(pm, repo_root)
    )
    local_path = _resolve_local_manifest_path(pm, repo_root, _logger)

    try:
        return build_effective_repo_graph(
            private_manifest_path=private_path,
            project_manifest_path=project_path,
            work_scope_manifest_path=work_scope_path,
            local_manifest_path=local_path,
        )
    except RepoGraphConfigError as exc:
        _logger.warning(
            "EffectiveRepoGraph construction failed "
            "(private=%s project=%s work_scope=%s local=%s): %s; "
            "continuing without graph context",
            private_path, project_path, work_scope_path, local_path, exc,
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
_PRIVATE_MANIFEST_REL_PATH = Path("manifests")
_PRIVATE_MANIFEST_FILE = "private_manifest.yaml"


def _resolve_private_manifest_path(pm, repo_root):
    if pm.private_manifest_path is not None:
        return pm.private_manifest_path
    if not pm.project_slug:
        return None
    roots: list[Path] = []
    if repo_root is not None:
        roots.append(Path(repo_root).resolve().parent)
    roots.append(Path.cwd())
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        explicit_child_candidates = sorted(
            root.glob(f"*/{_PRIVATE_MANIFEST_REL_PATH}/{pm.project_slug}/{_PRIVATE_MANIFEST_FILE}")
        )
        for candidate in explicit_child_candidates:
            if candidate.is_file():
                return candidate
    return None


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
        from platform_deployment_cli.local_manifest import discover_local_manifest  # ty:ignore[unresolved-import]
    except ImportError:
        try:
            from workstation_cli.local_manifest import discover_local_manifest  # ty:ignore[unresolved-import]
        except ImportError:
            logger.debug(
                "platform_manifest.project_slug=%r set but platform_deployment_cli/workstation_cli "
                "is not installed; skipping LocalManifest discovery",
                pm.project_slug,
            )
            return None
    try:
        return discover_local_manifest(pm.project_slug, repo_root=repo_root)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.debug(
            "PlatformDeployment LocalManifest discovery failed for slug=%r: %s; "
            "continuing without local layer",
            pm.project_slug, exc,
        )
        return None


__all__ = [
    "build_effective_repo_graph",
    "build_effective_repo_graph_from_settings",
]
