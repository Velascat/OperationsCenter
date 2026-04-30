# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Load managed repo config from YAML files."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import ManagedRepoConfig

_DEFAULT_CONFIG_DIR = Path(__file__).parent.parent.parent.parent.parent / "config" / "managed_repos"


def load_managed_repo_config(
    repo_id: str,
    *,
    config_dir: Path | str | None = None,
) -> ManagedRepoConfig:
    """Load a managed repo config by repo_id from the config/managed_repos/ directory."""
    base = Path(config_dir) if config_dir is not None else _DEFAULT_CONFIG_DIR
    path = base / f"{repo_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Managed repo config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ManagedRepoConfig.model_validate(raw)
