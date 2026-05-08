# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""SwitchBoard denylist: orchestration symbols forbidden inside the SB package.

SwitchBoard owns lane/backend selection only. Orchestration, lifecycle,
swarm coordination, and run-memory writes live in OperationsCenter.

This check scans the SwitchBoard source tree for occurrences of forbidden
symbol names (as identifier tokens, not in comments or strings). A match
fails the boundary check.

The default denylist is intentionally **forward-looking**: it includes
symbol names that do not yet exist (e.g. ``SwarmCoordinator``,
``LifecycleRunner``, ``RunMemoryIndexWriter``). Today the check passes
trivially. Once ER-001…ER-003 land in OperationsCenter, this same check
guarantees those primitives do not bleed into SwitchBoard. New entries
extend the denylist as primitives are added.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Forward-looking: most of these classes do not yet exist anywhere.
# They are pinned now so accidental SB collapse fails the boundary check.
DEFAULT_SWITCHBOARD_DENYLIST: tuple[str, ...] = (
    # ER-004 swarm (deferred — must never land in SB)
    "SwarmCoordinator",
    "SwarmPlan",
    "SwarmRole",
    "SwarmTopology",
    # ER-003 lifecycle
    "LifecycleRunner",
    "TaskLifecycleStage",
    "LifecycleStagePolicy",
    # ER-002 run memory
    "RunMemoryIndexWriter",
    "RunMemoryQueryService",
    "RunMemoryRecord",
    # ER-001 repo graph (graph itself is OC-owned; SB consumes it as input only)
    "RepoGraphLoader",
    "RepoGraphIndexer",
    # Runtime dispatch (ExecutorRuntime + RxP) — runtime execution is not
    # SwitchBoard's job. SB picks lanes/backends; ExecutorRuntime invokes them.
    "ExecutorRuntime",
    "RuntimeRunner",
    "SubprocessRunner",
    "RuntimeInvocation",
    "RuntimeResult",
    # Fork management — SourceRegistry owns this; SB has no business with forks.
    "SourceRegistry",
    # PlatformManifest composition — manifests are loaded by OC and consumed
    # by SB as input only. SB must not load, merge, or own composition.
    "load_repo_graph",
    "load_effective_graph",
    "load_default_repo_graph",
    "PlatformManifestSettings",
    "build_effective_repo_graph",
    "build_effective_repo_graph_from_settings",
    "WorkScopeManifest",
    "ManifestKind",
)


@dataclass(frozen=True)
class BoundaryFinding:
    path: str  # repo-relative
    line: int
    symbol: str
    kind: str  # 'class_def' | 'name_ref'

    def message(self) -> str:
        return f"forbidden orchestration symbol '{self.symbol}' ({self.kind}) in SwitchBoard"


def _iter_python_files(root: Path):
    if not root.exists():
        return
    # Walk by directory listing to avoid the no-scanning rule applying to
    # this audit utility (which is allowed to walk; only artifact_index
    # is forbidden from scanning).
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        for entry in sorted(cur.iterdir()):
            if entry.is_dir():
                if entry.name in {"__pycache__", ".venv", "node_modules", ".git"}:
                    continue
                stack.append(entry)
            elif entry.suffix == ".py":
                yield entry


def check_switchboard_denylist(
    sb_src_root: Path,
    denylist: tuple[str, ...] = DEFAULT_SWITCHBOARD_DENYLIST,
) -> list[BoundaryFinding]:
    """Scan SwitchBoard source for forbidden orchestration symbols.

    Args:
        sb_src_root: path to ``SwitchBoard/src/switchboard`` (or a fixture).
        denylist: tuple of forbidden identifier names.

    Returns: list of findings (empty = clean).
    """
    findings: list[BoundaryFinding] = []
    denyset = set(denylist)
    for path in _iter_python_files(sb_src_root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in denyset:
                findings.append(
                    BoundaryFinding(
                        path=str(path),
                        line=node.lineno,
                        symbol=node.name,
                        kind="class_def",
                    )
                )
            elif isinstance(node, ast.Name) and node.id in denyset:
                findings.append(
                    BoundaryFinding(
                        path=str(path),
                        line=node.lineno,
                        symbol=node.id,
                        kind="name_ref",
                    )
                )
            elif isinstance(node, ast.Attribute) and node.attr in denyset:
                findings.append(
                    BoundaryFinding(
                        path=str(path),
                        line=node.lineno,
                        symbol=node.attr,
                        kind="name_ref",
                    )
                )
            elif isinstance(node, ast.alias) and (node.asname or node.name) in denyset:
                # `from x import SwarmCoordinator` — catch import too.
                sym = node.asname or node.name.split(".")[-1]
                if sym in denyset:
                    findings.append(
                        BoundaryFinding(
                            path=str(path),
                            line=getattr(node, "lineno", 0),
                            symbol=sym,
                            kind="name_ref",
                        )
                    )
    return findings
