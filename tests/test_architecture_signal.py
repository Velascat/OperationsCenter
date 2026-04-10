"""Unit tests for ArchitectureSignalCollector static helpers."""

from __future__ import annotations

from pathlib import PurePosixPath

from control_plane.observer.collectors.architecture_signal import (
    ArchitectureSignalCollector,
)


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
        assert " -> " in cycles[0]

    def test_multi_node_cycle(self):
        graph = {"A": {"B"}, "B": {"C"}, "C": {"A"}}
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1
        parts = cycles[0].split(" -> ")
        assert len(parts) == 4
        assert parts[0] == parts[-1]

    def test_self_loop(self):
        graph = {"A": {"A"}}
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1
        assert cycles[0] == "A -> A"

    def test_neighbor_not_in_graph_is_skipped(self):
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
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        graph[nodes[-1]] = set()
        assert ArchitectureSignalCollector._detect_cycles(graph) == []

    def test_deep_chain_with_cycle_at_end(self):
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        cycle_start = 1990
        graph[nodes[-1]] = {nodes[cycle_start]}
        cycles = ArchitectureSignalCollector._detect_cycles(graph)
        assert len(cycles) == 1
        parts = cycles[0].split(" -> ")
        assert parts[0] == parts[-1]
        assert len(parts) == (2000 - cycle_start) + 1


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
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 1

    def test_diamond_shape(self):
        graph = {"A": {"B", "C"}, "B": {"D"}, "C": {"D"}, "D": set()}
        module_set = {"A", "B", "C", "D"}
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 2

    def test_single_node_no_deps(self):
        graph = {"A": set()}
        module_set = {"A"}
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 0

    def test_cycle_terminates(self):
        graph = {"A": {"B"}, "B": {"A"}}
        module_set = {"A", "B"}
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 1

    def test_disconnected_components(self):
        graph = {"A": {"B"}, "B": set(), "C": {"D"}, "D": {"E"}, "E": set()}
        module_set = {"A", "B", "C", "D", "E"}
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 2

    def test_node_not_in_graph_keys(self):
        graph = {"A": {"B"}}
        module_set = {"A", "B"}
        assert ArchitectureSignalCollector._compute_max_import_depth(graph, module_set) == 1

    def test_deep_chain_no_recursion_error(self):
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        graph[nodes[-1]] = set()
        module_set = set(nodes)
        assert (
            ArchitectureSignalCollector._compute_max_import_depth(graph, module_set)
            == 1999
        )

    def test_deep_chain_with_cycle_terminates(self):
        nodes = [f"mod_{i}" for i in range(2000)]
        graph = {nodes[i]: {nodes[i + 1]} for i in range(len(nodes) - 1)}
        graph[nodes[-1]] = {nodes[0]}
        module_set = set(nodes)
        result = ArchitectureSignalCollector._compute_max_import_depth(graph, module_set)
        assert result == 1999


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
