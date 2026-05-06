# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Deprecation shim: ``operations-center-upstream`` → ``source-registry``.

The fork-manager engine moved out of OperationsCenter into the
SourceRegistry library. This entrypoint stays one release for
muscle-memory compatibility — it prints a deprecation notice and
delegates to ``source-registry`` with OC's canonical paths
(``registry/source_registry.yaml`` and ``registry/patches``)
auto-injected so existing scripts keep working.

Hard-cut planned for the next release.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Canonical OC paths for the source registry + patches.
# Resolved relative to the OperationsCenter repo root (the directory
# containing ``registry/source_registry.yaml``). The shim walks up from
# the CWD until it finds that file, falling back to the literal path.
_REGISTRY_RELATIVE = Path("registry/source_registry.yaml")
_PATCHES_RELATIVE = Path("registry/patches")


def _find_registry_root() -> Path:
    """Walk up from CWD looking for ``registry/source_registry.yaml``."""
    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        if (parent / _REGISTRY_RELATIVE).is_file():
            return parent
    return cwd


def _emit_deprecation() -> None:
    sys.stderr.write(
        "\033[33m[deprecated]\033[0m operations-center-upstream is now a thin "
        "shim over `source-registry`.\n"
        "  Switch to: source-registry <subcommand> --registry "
        f"{_REGISTRY_RELATIVE} [--patches {_PATCHES_RELATIVE}]\n\n"
    )


# Subcommands that need ``--patches`` auto-injected on the SR side
_PATCHES_SUBCOMMANDS = {"poll", "push", "drop"}

# Subcommands the old CLI supported but SR maps differently
_LEGACY_INSTALL_NOTE = (
    "\033[33m[deprecated]\033[0m `install` was removed; use "
    "`sync` (rebase + bump + reinstall) or `auto-sync` instead.\n"
)


def main() -> None:
    _emit_deprecation()

    argv = list(sys.argv[1:])
    if not argv:
        # No subcommand → show help via the SR CLI
        from source_registry.cli import app
        app(["--help"])
        return

    subcommand = argv[0]

    if subcommand == "install":
        sys.stderr.write(_LEGACY_INSTALL_NOTE)
        sys.exit(2)

    # Auto-inject --registry pointing at OC's canonical path
    root = _find_registry_root()
    registry_path = root / _REGISTRY_RELATIVE
    patches_path = root / _PATCHES_RELATIVE

    user_supplied_registry = "--registry" in argv
    user_supplied_patches = "--patches" in argv

    new_argv = argv[:]
    if not user_supplied_registry:
        new_argv += ["--registry", str(registry_path)]
    if subcommand in _PATCHES_SUBCOMMANDS and not user_supplied_patches:
        new_argv += ["--patches", str(patches_path)]

    # Hand off to the SR CLI app
    from source_registry.cli import app
    app(new_argv)


if __name__ == "__main__":
    main()
