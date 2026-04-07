"""Tests for Phase 5 observer collectors: architecture, benchmark, security."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from control_plane.observer.collectors.architecture_signal import ArchitectureSignalCollector
from control_plane.observer.collectors.benchmark_signal import BenchmarkSignalCollector
from control_plane.observer.collectors.security_signal import SecuritySignalCollector
from control_plane.observer.models import ArchitectureSignal, BenchmarkSignal, SecuritySignal
from control_plane.observer.service import ObserverContext


def _make_context(repo_path: Path, logs_root: Path | None = None) -> ObserverContext:
    """Create a minimal ObserverContext for testing."""
    return ObserverContext(
        repo_path=repo_path,
        repo_name="test-repo",
        base_branch="main",
        run_id="obs_test_001",
        observed_at=datetime.now(UTC),
        source_command="test",
        settings=MagicMock(),
        commit_limit=10,
        hotspot_window=30,
        todo_limit=20,
        logs_root=logs_root or repo_path / "logs",
    )


# ── ArchitectureSignalCollector ──────────────────────────────────────


class TestArchitectureSignalCollector:
    def test_unavailable_when_no_src_dir(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        signal = ArchitectureSignalCollector().collect(ctx)
        assert isinstance(signal, ArchitectureSignal)
        assert signal.status == "unavailable"

    def test_unavailable_when_src_empty(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        ctx = _make_context(tmp_path)
        signal = ArchitectureSignalCollector().collect(ctx)
        assert signal.status == "unavailable"

    def test_healthy_with_simple_modules(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "alpha.py").write_text("import os\n")
        (src / "beta.py").write_text("import sys\n")
        ctx = _make_context(tmp_path)
        signal = ArchitectureSignalCollector().collect(ctx)
        assert signal.status == "healthy"
        assert signal.source == "static_analysis"
        assert signal.max_import_depth is not None
        assert signal.coupling_score is not None
        assert signal.observed_at is not None

    def test_detects_circular_dependency(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("import b\n")
        (src / "b.py").write_text("import a\n")
        ctx = _make_context(tmp_path)
        signal = ArchitectureSignalCollector().collect(ctx)
        assert signal.status == "warnings"
        assert len(signal.circular_dependencies) > 0

    def test_handles_syntax_error_in_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "good.py").write_text("import os\n")
        (src / "bad.py").write_text("def foo(\n")  # syntax error
        ctx = _make_context(tmp_path)
        signal = ArchitectureSignalCollector().collect(ctx)
        # Should still succeed (skips bad file)
        assert signal.status in ("healthy", "warnings")

    def test_nested_packages(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        pkg = src / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "core.py").write_text("import os\nimport sys\n")
        (pkg / "utils.py").write_text("import mypkg\n")
        ctx = _make_context(tmp_path)
        signal = ArchitectureSignalCollector().collect(ctx)
        assert signal.status in ("healthy", "warnings")
        assert signal.max_import_depth is not None


# ── BenchmarkSignalCollector ─────────────────────────────────────────


class TestBenchmarkSignalCollector:
    def test_unavailable_when_no_files(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        signal = BenchmarkSignalCollector().collect(ctx)
        assert isinstance(signal, BenchmarkSignal)
        assert signal.status == "unavailable"

    def test_parses_pytest_benchmark(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        data = {
            "benchmarks": [
                {"name": "test_fast", "stats": {"mean": 0.001, "stddev": 0.0001}},
                {"name": "test_slow", "stats": {"mean": 0.5, "stddev": 0.01}},
            ]
        }
        (logs / "run.benchmark.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = BenchmarkSignalCollector().collect(ctx)
        assert signal.status == "nominal"
        assert signal.benchmark_count == 2
        assert signal.source == "pytest_benchmark"
        assert len(signal.regressions) == 0

    def test_detects_regression(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        data = {
            "benchmarks": [
                {"name": "test_regressed", "stats": {"mean": 0.01, "stddev": 0.05}},
            ]
        }
        (logs / "perf.benchmark.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = BenchmarkSignalCollector().collect(ctx)
        assert signal.status == "regression"
        assert len(signal.regressions) == 1
        assert "test_regressed" in signal.regressions[0]

    def test_parses_hyperfine(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        data = {
            "results": [
                {"command": "echo hello", "mean": 0.002, "stddev": 0.0001},
            ]
        }
        (logs / "hyperfine_results.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = BenchmarkSignalCollector().collect(ctx)
        assert signal.status == "nominal"
        assert signal.benchmark_count == 1
        assert signal.source == "hyperfine"

    def test_ignores_invalid_json(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "bad.benchmark.json").write_text("not json {{{")
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = BenchmarkSignalCollector().collect(ctx)
        assert signal.status == "unavailable"

    def test_searches_repo_path_too(self, tmp_path: Path) -> None:
        """Benchmark files found in repo_path should also be discovered."""
        bdir = tmp_path / "pytest-benchmark"
        bdir.mkdir()
        data = {"benchmarks": [{"name": "t", "stats": {"mean": 1.0, "stddev": 0.1}}]}
        (bdir / "0001_results.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=tmp_path / "nonexistent")
        signal = BenchmarkSignalCollector().collect(ctx)
        assert signal.status == "nominal"
        assert signal.benchmark_count == 1


# ── SecuritySignalCollector ──────────────────────────────────────────


class TestSecuritySignalCollector:
    def test_unavailable_when_no_files(self, tmp_path: Path) -> None:
        ctx = _make_context(tmp_path)
        signal = SecuritySignalCollector().collect(ctx)
        assert isinstance(signal, SecuritySignal)
        assert signal.status == "unavailable"

    def test_parses_pip_audit_clean(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        data = [{"name": "requests", "version": "2.28.0", "vulns": []}]
        (logs / "pip-audit.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = SecuritySignalCollector().collect(ctx)
        assert signal.status == "clean"
        assert signal.advisory_count == 0

    def test_parses_pip_audit_with_vulns(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        data = [
            {
                "name": "requests",
                "version": "2.25.0",
                "vulns": [
                    {"id": "CVE-2021-1234", "fix_versions": ["2.26.0"]},
                    {"id": "CVE-2021-5678", "fix_versions": ["2.26.0"]},
                ],
            }
        ]
        (logs / "pip-audit-output.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = SecuritySignalCollector().collect(ctx)
        assert signal.status == "advisories"
        assert signal.advisory_count == 2
        assert signal.source == "pip_audit"

    def test_parses_npm_audit(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        data = {
            "vulnerabilities": {
                "lodash": {"severity": "critical"},
                "express": {"severity": "high"},
                "debug": {"severity": "low"},
            }
        }
        (logs / "npm-audit.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = SecuritySignalCollector().collect(ctx)
        assert signal.status == "advisories"
        assert signal.advisory_count == 3
        assert signal.critical_count == 1
        assert signal.high_count == 1
        assert signal.source == "npm_audit"

    def test_parses_trivy(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        data = {
            "Results": [
                {
                    "Vulnerabilities": [
                        {"VulnerabilityID": "CVE-2022-0001", "Severity": "CRITICAL"},
                        {"VulnerabilityID": "CVE-2022-0002", "Severity": "HIGH"},
                        {"VulnerabilityID": "CVE-2022-0003", "Severity": "MEDIUM"},
                    ]
                }
            ]
        }
        (logs / "trivy-results.json").write_text(json.dumps(data))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = SecuritySignalCollector().collect(ctx)
        assert signal.status == "advisories"
        assert signal.advisory_count == 3
        assert signal.critical_count == 1
        assert signal.high_count == 1
        assert signal.source == "trivy"

    def test_ignores_invalid_json(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "pip-audit-bad.json").write_text("nope")
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = SecuritySignalCollector().collect(ctx)
        assert signal.status == "unavailable"

    def test_multiple_sources(self, tmp_path: Path) -> None:
        logs = tmp_path / "logs"
        logs.mkdir()
        (logs / "pip-audit.json").write_text(json.dumps([]))
        (logs / "npm-audit.json").write_text(json.dumps({"vulnerabilities": {}}))
        ctx = _make_context(tmp_path, logs_root=logs)
        signal = SecuritySignalCollector().collect(ctx)
        assert signal.status == "clean"
        assert "pip_audit" in signal.source
        assert "npm_audit" in signal.source


# ── Wiring test ──────────────────────────────────────────────────────


class TestBuildObserverServiceWiring:
    def test_import_and_build(self) -> None:
        """Verify that build_observer_service imports and constructs without error."""
        from control_plane.entrypoints.autonomy_cycle.main import build_observer_service

        service = build_observer_service()
        assert service.architecture_signal_collector is not None
        assert service.benchmark_signal_collector is not None
        assert service.security_signal_collector is not None
