"""Unit tests for ArchitectureSignalCollector static helpers."""

from __future__ import annotations

from pathlib import PurePosixPath

from control_plane.observer.collectors.architecture_signal import (
    ArchitectureSignalCollector,
)


# ── _detect_cycles ──────────────────────────────────────────────────


class TestDetectCycles:
    """Tests for ArchitectureSignalCollector._detect_cycles."""

    def test_empty_graph(self):
        assert ArchitectureSignalCollector._detect_cycles({}) == []

    def test_acyclic_graph(self):
        graph = {"A": {"B"}, "B": {"C"}, "C": set()}
        assert ArchitectureSignalCollector._detect_cycles(graph) == []

    def test_simple_two_node_cycle(self):
        graph = {"A": {"B"}, "B": {"A"}}
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1
        assert "A" in cycles[0] and "B" in cycles[0]
        # The cycle description should contain "A -> B -> A" or "B -> A -> B"
        assert " -> " in cycles[0]

    def test_multi_node_cycle(self):
        graph = {"A": {"B"}, "B": {"C"}, "C": {"A"}}
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1
        # Should describe a 3-node cycle
        parts = cycles[0].split(" -> ")
        assert len(parts) == 4  # e.g. "A -> B -> C -> A"
        assert parts[0] == parts[-1]  # start == end

    def test_self_loop(self):
        graph = {"A": {"A"}}
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1
        assert cycles[0] == "A -> A"

    def test_neighbor_not_in_graph_is_skipped(self):
        # "B" is referenced but not a key in the graph → no cycle
        graph = {"A": {"B"}}
        assert ArchitectureSignalCollector._detect_cycles(graph) == []

    def test_disconnected_components_one_cycle(self):
        graph = {
            "A": {"B"},
            "B": {"A"},
            "C": {"D"},
            "D": set(),
        }
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1

    def test_multiple_independent_cycles(self):
        graph = {
            "A": {"B"},
            "B": {"A"},
            "C": {"D"},
            "D": {"C"},
        }
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 2

    def test_deep_chain_no_recursion_error(self):
        """A 2000-node linear chain must complete without RecursionError."""
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        graph[nodes[-1]] = set()
        # Should return no cycles and not raise RecursionError
        assert ArchitectureSignalCollector._detect_cycles(graph) == []

    def test_deep_chain_with_cycle_at_end(self):
        """A 2000-node chain with a back-edge at the end detects the cycle."""
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        # Create a cycle: last node points back to node 1990
        cycle_start = 1990
        graph[nodes[-1]] = {nodes[cycle_start]}
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1
        # Verify the cycle contains the expected nodes
        parts = cycles[0].split(" -> ")
        assert parts[0] == parts[-1]  # start == end
        assert len(parts) == (2000 - cycle_start) + 1  # 10 nodes + back to start


# ── _compute_max_import_depth ───────────────────────────────────────


class TestComputeMaxImportDepth:
    """Tests for ArchitectureSignalCollector._compute_max_import_depth."""

    def test_empty(self):
        assert ArchitectureSignalCollector._compute_max_import_depth({}, set()) == 0

    def test_linear_chain(self):
        graph = {"A": {"B"}, "B": {"C"}, "C": set()}
        module_set = {"A", "B", "C"}
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 2

    def test_deps_outside_module_set_ignored(self):
        graph = {"A": {"B", "X"}, "B": set()}
        module_set = {"A", "B"}
        # X is not in module_set, so only A→B counts → depth 1
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 1

    def test_diamond_shape(self):
        #   A
        #  / \
        # B   C
        #  \ /
        #   D
        graph = {"A": {"B", "C"}, "B": {"D"}, "C": {"D"}, "D": set()}
        module_set = {"A", "B", "C", "D"}
        # Longest chain: A→B→D or A→C→D → depth 2
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 2

    def test_single_node_no_deps(self):
        graph = {"A": set()}
        module_set = {"A"}
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 0

    def test_cycle_terminates(self):
        """BFS on a cyclic graph must terminate thanks to visited tracking."""
        graph = {"A": {"B"}, "B": {"A"}}
        module_set = {"A", "B"}
        # A→B is depth 1; B→A already visited so stops
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 1

    def test_disconnected_components(self):
        """Max depth considers all components."""
        graph = {"A": {"B"}, "B": set(), "C": {"D"}, "D": {"E"}, "E": set()}
        module_set = {"A", "B", "C", "D", "E"}
        # Component 1: A→B (depth 1), Component 2: C→D→E (depth 2)
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 2

    def test_node_not_in_graph_keys(self):
        """Module in module_set but not in graph keys is handled safely."""
        graph = {"A": {"B"}}
        module_set = {"A", "B"}
        # B has no entry in graph → graph.get(B, set()) returns empty set
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 1

    def test_deep_chain_no_recursion_error(self):
        """A 2000-node linear chain must complete without RecursionError."""
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        graph[nodes[-1]] = set()
        module_set = set(nodes)
        assert (
            ArchitectureSignalCollector._compute_max_import_depth(graph, module_set)
            == 1999
        )

    def test_deep_chain_with_cycle_terminates(self):
        """A 2000-node chain with a back-edge terminates via visited tracking."""
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        graph[nodes[-1]] = {nodes[0]}  # back-edge creating cycle
        module_set = set(nodes)
        # BFS should still terminate; depth is 1999 (longest BFS layer distance)
        result = ArchitectureSignalCollector._compute_max_import_depth(graph, module_set)
        assert result == 1999


# ── _path_to_module ────────────────────────────────────────────────


class TestPathToModule:
    """Tests for ArchitectureSignalCollector._path_to_module."""

    def test_regular_file(self):
        result = ArchitectureSignalCollector._path_to_module(
            PurePosixPath("src/foo/bar.py"), PurePosixPath("src")
        )
        assert result == "foo.bar"

    def test_init_file(self):
        result = ArchitectureSignalCollector._path_to_module(
            PurePosixPath("src/foo/__init__.py"), PurePosixPath("src")
        )
        assert result == "foo"

    def test_root_init(self):
        result = ArchitectureSignalCollector._path_to_module(
            PurePosixPath("src/__init__.py"), PurePosixPath("src")
        )
        assert result == "__root__"

    def test_deeply_nested(self):
        result = ArchitectureSignalCollector._path_to_module(
            PurePosixPath("src/a/b/c/mod.py"), PurePosixPath("src")
        )
        assert result == "a.b.c.mod"
