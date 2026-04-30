# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import ast
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from operations_center.observer.models import ArchitectureSignal
from operations_center.observer.service import ObserverContext


class ArchitectureSignalCollector:
    """Static coupling / import-depth analysis of the source tree.

    Walks Python files under ``context.repo_path / "src"`` and computes:
    * max import depth per module
    * circular dependency detection (simple cycle detection)
    * coupling score (cross-module imports / total modules)

    Never runs external tools or modifies the repository.
    """

    def collect(self, context: ObserverContext) -> ArchitectureSignal:
        try:
            return self._analyze(context)
        except Exception:
            return ArchitectureSignal(status="unavailable")

    # ------------------------------------------------------------------

    def _analyze(self, context: ObserverContext) -> ArchitectureSignal:
        src_dir = context.repo_path / "src"
        if not src_dir.is_dir():
            return ArchitectureSignal(status="unavailable")

        py_files = list(src_dir.rglob("*.py"))
        if not py_files:
            return ArchitectureSignal(status="unavailable")

        # Build import graph: module -> set of imported modules
        import_graph: dict[str, set[str]] = defaultdict(set)
        module_set: set[str] = set()

        for py_file in py_files:
            module_name = self._path_to_module(py_file, src_dir)
            module_set.add(module_name)
            imports = self._extract_imports(py_file)
            import_graph[module_name] = imports

        # Compute max import depth (longest chain of imports reachable)
        max_depth = self._compute_max_import_depth(import_graph, module_set)

        # Detect circular dependencies
        cycles = self._detect_cycles(import_graph)

        # Coupling score: ratio of cross-module import edges to total modules
        total_modules = len(module_set)
        cross_module_imports = sum(
            len(targets & module_set) for targets in import_graph.values()
        )
        coupling_score = round(cross_module_imports / total_modules, 3) if total_modules > 0 else 0.0

        has_warnings = bool(cycles) or coupling_score > 5.0 or max_depth > 8
        status = "warnings" if has_warnings else "healthy"

        parts: list[str] = []
        parts.append(f"{total_modules} modules analyzed")
        parts.append(f"max import depth {max_depth}")
        if cycles:
            parts.append(f"{len(cycles)} circular dependency(ies)")
        parts.append(f"coupling score {coupling_score}")

        return ArchitectureSignal(
            status=status,
            source="static_analysis",
            observed_at=datetime.now(UTC),
            max_import_depth=max_depth,
            circular_dependencies=cycles,
            coupling_score=coupling_score,
            summary="; ".join(parts),
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _path_to_module(py_file: Path, src_dir: Path) -> str:
        rel = py_file.relative_to(src_dir)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1].removesuffix(".py")
        return ".".join(parts) if parts else "__root__"

    @staticmethod
    def _extract_imports(py_file: Path) -> set[str]:
        """Parse a Python file's AST and return top-level imported module names."""
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, ValueError):
            return set()

        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        return imports

    @staticmethod
    def _compute_max_import_depth(
        graph: dict[str, set[str]], module_set: set[str]
    ) -> int:
        """BFS from each module to find the longest chain within known modules."""
        max_depth = 0
        for start in module_set:
            visited: set[str] = {start}
            frontier = [start]
            depth = 0
            while frontier:
                next_frontier: list[str] = []
                for mod in frontier:
                    for dep in graph.get(mod, set()):
                        if dep in module_set and dep not in visited:
                            visited.add(dep)
                            next_frontier.append(dep)
                if next_frontier:
                    depth += 1
                frontier = next_frontier
            max_depth = max(max_depth, depth)
        return max_depth

    @staticmethod
    def _detect_cycles(graph: dict[str, set[str]]) -> list[str]:
        """Simple DFS-based cycle detection. Returns list of cycle descriptions."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = defaultdict(int)
        cycles: list[str] = []
        seen_cycles: set[frozenset[str]] = set()

        for start in list(graph.keys()):
            if color[start] != WHITE:
                continue
            path: list[str] = []
            # Stack holds (node, neighbors_iterator) pairs.
            # A None iterator means "start processing this node" (pre-visit).
            # Termination: each node transitions WHITE->GRAY->BLACK monotonically;
            # only WHITE nodes are pushed, so the stack is bounded by |V|.
            stack: list[tuple[str, Iterator[str] | None]] = [(start, None)]
            while stack:
                node, neighbors = stack[-1]
                if neighbors is None:
                    # Pre-visit: color GRAY, add to path, create iterator
                    color[node] = GRAY
                    path.append(node)
                    stack[-1] = (node, iter(graph.get(node, set())))
                    continue
                # Try to advance to the next neighbor
                neighbor = next(neighbors, None)
                if neighbor is None:
                    # Post-visit: all neighbors processed
                    stack.pop()
                    path.pop()
                    color[node] = BLACK
                else:
                    if neighbor not in graph:
                        continue
                    if color[neighbor] == GRAY:
                        idx = path.index(neighbor)
                        cycle_members = path[idx:]
                        key = frozenset(cycle_members)
                        if key not in seen_cycles:
                            seen_cycles.add(key)
                            cycles.append(" -> ".join(cycle_members + [neighbor]))
                    elif color[neighbor] == WHITE:
                        stack.append((neighbor, None))

        return cycles
