from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from control_plane.observer.models import BenchmarkSignal
from control_plane.observer.service import ObserverContext

# Glob patterns for benchmark artifact discovery
_BENCHMARK_PATTERNS = [
    "*.benchmark.json",
    "pytest-benchmark/*.json",
    "hyperfine_*.json",
    "report.json",
]


class BenchmarkSignalCollector:
    """Read pre-existing benchmark output files from retained artifacts.

    Supports pytest-benchmark and hyperfine JSON formats.
    NEVER runs benchmarks — only reads existing output files.
    """

    def collect(self, context: ObserverContext) -> BenchmarkSignal:
        try:
            return self._analyze(context)
        except Exception:
            return BenchmarkSignal(status="unavailable")

    # ------------------------------------------------------------------

    def _analyze(self, context: ObserverContext) -> BenchmarkSignal:
        search_roots = [context.logs_root, context.repo_path]
        seen: set[Path] = set()
        found_files: list[Path] = []
        for root in search_roots:
            if root.is_dir():
                for pattern in _BENCHMARK_PATTERNS:
                    for f in root.rglob(pattern):
                        resolved = f.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            found_files.append(f)

        if not found_files:
            return BenchmarkSignal(status="unavailable")

        total_benchmarks = 0
        regressions: list[str] = []
        source_set: set[str] = set()

        for fpath in found_files:
            try:
                data = json.loads(fpath.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue

            if isinstance(data, dict):
                if "benchmarks" in data:
                    count, regs = self._parse_pytest_benchmark(data)
                    total_benchmarks += count
                    regressions.extend(regs)
                    source_set.add("pytest_benchmark")
                elif "results" in data:
                    count, regs = self._parse_hyperfine(data)
                    total_benchmarks += count
                    regressions.extend(regs)
                    source_set.add("hyperfine")

        if total_benchmarks == 0:
            return BenchmarkSignal(status="unavailable")

        status = "regression" if regressions else "nominal"
        source = ",".join(sorted(source_set)) or "unknown"

        parts: list[str] = [f"{total_benchmarks} benchmark(s) found"]
        if regressions:
            parts.append(f"{len(regressions)} regression(s)")

        return BenchmarkSignal(
            status=status,
            source=source,
            observed_at=datetime.now(UTC),
            benchmark_count=total_benchmarks,
            regressions=regressions,
            summary="; ".join(parts),
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_pytest_benchmark(data: dict) -> tuple[int, list[str]]:
        """Parse pytest-benchmark JSON. Returns (count, regressions)."""
        benchmarks = data.get("benchmarks", [])
        if not isinstance(benchmarks, list):
            return 0, []

        regressions: list[str] = []
        for bench in benchmarks:
            stats = bench.get("stats", {})
            mean = stats.get("mean", 0)
            stddev = stats.get("stddev", 0)
            name = bench.get("name", "unknown")
            if mean > 0 and stddev > 2 * mean:
                regressions.append(f"{name}: stddev ({stddev:.4g}) > 2x mean ({mean:.4g})")

        return len(benchmarks), regressions

    @staticmethod
    def _parse_hyperfine(data: dict) -> tuple[int, list[str]]:
        """Parse hyperfine JSON. Returns (count, regressions)."""
        results = data.get("results", [])
        if not isinstance(results, list):
            return 0, []

        regressions: list[str] = []
        for result in results:
            mean = result.get("mean", 0)
            stddev = result.get("stddev", 0)
            command = result.get("command", "unknown")
            if mean > 0 and stddev > 2 * mean:
                regressions.append(f"{command}: stddev ({stddev:.4g}) > 2x mean ({mean:.4g})")

        return len(results), regressions
