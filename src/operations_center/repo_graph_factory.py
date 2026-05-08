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


__all__ = ["build_effective_repo_graph"]
