# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""CoverageSignalCollector — reads retained coverage reports without running coverage tools.

Supports:
  - coverage.xml   (Cobertura XML — output of ``coverage xml``)
  - .coverage      (presence-only; version detected from header)
  - pytest-coverage.txt / coverage.txt  (text report)
  - htmlcov/index.html  (HTML report title contains "X%")

NEVER runs coverage tools.  Only reads files that already exist.
Returns CoverageSignal(status="unavailable") when no report is found.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from operations_center.observer.models import CoverageSignal, UncoveredFile
from operations_center.observer.service import ObserverContext

_UNCOVERED_THRESHOLD_PCT = 80.0   # files below this are listed as under-covered
_MAX_UNCOVERED_LISTED = 10
_TEXT_TOTAL_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+)%")
_HTML_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


class CoverageSignalCollector:
    """Reads pre-existing coverage reports to surface coverage gaps.

    Checks (in priority order):
    1. ``coverage.xml`` — full file-level data
    2. ``pytest-coverage.txt`` or ``coverage.txt`` — text totals only
    3. ``htmlcov/index.html`` — HTML summary
    """

    def collect(self, context: ObserverContext) -> CoverageSignal:
        try:
            return self._analyze(context)
        except Exception:
            return CoverageSignal(status="unavailable")

    def _analyze(self, context: ObserverContext) -> CoverageSignal:
        search_roots = [context.repo_path]
        if context.logs_root.is_dir():
            search_roots.append(context.logs_root)

        for root in search_roots:
            # 1. Cobertura XML
            xml_path = root / "coverage.xml"
            if xml_path.is_file():
                result = self._parse_xml(xml_path)
                if result is not None:
                    return result

        for root in search_roots:
            # 2. Text report
            for name in ("pytest-coverage.txt", "coverage.txt", ".coverage_report.txt"):
                txt_path = root / name
                if txt_path.is_file():
                    result = self._parse_text(txt_path)
                    if result is not None:
                        return result

        for root in search_roots:
            # 3. HTML report
            html_path = root / "htmlcov" / "index.html"
            if html_path.is_file():
                result = self._parse_html(html_path)
                if result is not None:
                    return result

            # 4. .coverage file: presence-only signal
            cov_path = root / ".coverage"
            if cov_path.is_file():
                return CoverageSignal(
                    status="partial",
                    source=".coverage",
                    observed_at=datetime.now(UTC),
                    summary=".coverage file found but no report generated yet",
                )

        return CoverageSignal(status="unavailable")

    # ------------------------------------------------------------------

    def _parse_xml(self, path: Path) -> CoverageSignal | None:
        try:
            tree = ET.parse(path)
        except ET.ParseError:
            return None
        root = tree.getroot()
        rate_str = root.get("line-rate")
        if rate_str is None:
            return None
        try:
            total_pct = round(float(rate_str) * 100, 1)
        except ValueError:
            return None

        uncovered: list[UncoveredFile] = []
        for cls in root.iter("class"):
            cls_rate = cls.get("line-rate")
            cls_name = cls.get("filename") or cls.get("name") or "unknown"
            try:
                pct = round(float(cls_rate) * 100, 1) if cls_rate else 0.0
            except (ValueError, TypeError):
                pct = 0.0
            if pct < _UNCOVERED_THRESHOLD_PCT:
                uncovered.append(UncoveredFile(path=cls_name, coverage_pct=pct))

        uncovered.sort(key=lambda u: u.coverage_pct)
        top = uncovered[:_MAX_UNCOVERED_LISTED]
        summary = f"{total_pct}% overall coverage; {len(uncovered)} file(s) below {_UNCOVERED_THRESHOLD_PCT}%"
        return CoverageSignal(
            status="measured",
            total_coverage_pct=total_pct,
            uncovered_file_count=len(uncovered),
            uncovered_threshold_pct=_UNCOVERED_THRESHOLD_PCT,
            top_uncovered=top,
            source="coverage.xml",
            observed_at=datetime.now(UTC),
            summary=summary,
        )

    def _parse_text(self, path: Path) -> CoverageSignal | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        m = _TEXT_TOTAL_RE.search(text)
        if not m:
            return None
        total_pct = float(m.group(1))
        summary = f"{total_pct}% overall coverage (text report)"
        return CoverageSignal(
            status="measured",
            total_coverage_pct=total_pct,
            source=path.name,
            observed_at=datetime.now(UTC),
            summary=summary,
        )

    def _parse_html(self, path: Path) -> CoverageSignal | None:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        # Look for patterns like "Coverage: 74%" or "74% coverage" in the HTML
        m = _HTML_PCT_RE.search(text[:2000])
        if not m:
            return None
        total_pct = float(m.group(1))
        if total_pct > 100:
            return None
        return CoverageSignal(
            status="measured",
            total_coverage_pct=total_pct,
            source="htmlcov/index.html",
            observed_at=datetime.now(UTC),
            summary=f"{total_pct}% overall coverage (HTML report)",
        )
