# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Load managed repo config from YAML files.

Lookup order for ``{repo_id}.yaml``:

  1. ``config/managed_repos/local/{repo_id}.yaml`` — operator's private
     binding (gitignored). This is where real bound managed repos live.
  2. ``config/managed_repos/{repo_id}.yaml`` — tracked templates +
     example bindings only. Public repos must not commit private
     repo identities here.

The split keeps OperationsCenter's public source describing capabilities
while operators bind those capabilities to specific private repos in a
location that never enters the public history.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import ManagedRepoConfig

_DEFAULT_CONFIG_DIR = Path(__file__).parent.parent.parent.parent.parent / "config" / "managed_repos"
_LOCAL_SUBDIR = "local"


def load_managed_repo_config(
    repo_id: str,
    *,
    config_dir: Path | str | None = None,
) -> ManagedRepoConfig:
    """Load a managed repo config by repo_id.

    Searches ``{config_dir}/local/{repo_id}.yaml`` first (private
    binding) and falls back to ``{config_dir}/{repo_id}.yaml`` (tracked
    template). Raises FileNotFoundError when neither exists.
    """
    base = Path(config_dir) if config_dir is not None else _DEFAULT_CONFIG_DIR
    candidates = [
        base / _LOCAL_SUBDIR / f"{repo_id}.yaml",
        base / f"{repo_id}.yaml",
    ]
    for path in candidates:
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            return ManagedRepoConfig.model_validate(raw)
    raise FileNotFoundError(
        f"Managed repo config not found for repo_id={repo_id!r}; "
        f"looked in {candidates[0]} then {candidates[1]}"
    )
