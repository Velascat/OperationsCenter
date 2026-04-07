from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from control_plane.observer.models import SecuritySignal
from control_plane.observer.service import ObserverContext

# Glob patterns for audit artifact discovery
_AUDIT_PATTERNS = [
    "pip-audit*.json",
    "npm-audit*.json",
    "trivy*.json",
]


class SecuritySignalCollector:
    """Read pip-audit / npm-audit / trivy JSON from retained artifacts.

    NEVER runs audit tools — only reads existing output files.
    """

    def collect(self, context: ObserverContext) -> SecuritySignal:
        try:
            return self._analyze(context)
        except Exception:
            return SecuritySignal(status="unavailable")

    # ------------------------------------------------------------------

    def _analyze(self, context: ObserverContext) -> SecuritySignal:
        search_roots = [context.logs_root, context.repo_path]
        seen: set[Path] = set()
        found_files: list[Path] = []
        for root in search_roots:
            if root.is_dir():
                for pattern in _AUDIT_PATTERNS:
                    for f in root.rglob(pattern):
                        resolved = f.resolve()
                        if resolved not in seen:
                            seen.add(resolved)
                            found_files.append(f)

        if not found_files:
            return SecuritySignal(status="unavailable")

        total_advisories = 0
        total_critical = 0
        total_high = 0
        source_set: set[str] = set()

        for fpath in found_files:
            try:
                data = json.loads(fpath.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue

            fname = fpath.name.lower()
            if fname.startswith("pip-audit"):
                adv, crit, high = self._parse_pip_audit(data)
                source_set.add("pip_audit")
            elif fname.startswith("npm-audit"):
                adv, crit, high = self._parse_npm_audit(data)
                source_set.add("npm_audit")
            elif fname.startswith("trivy"):
                adv, crit, high = self._parse_trivy(data)
                source_set.add("trivy")
            else:
                continue

            total_advisories += adv
            total_critical += crit
            total_high += high

        if not source_set:
            return SecuritySignal(status="unavailable")

        status = "advisories" if total_advisories > 0 else "clean"
        source = ",".join(sorted(source_set))

        parts: list[str] = [f"{total_advisories} advisory(ies)"]
        if total_critical:
            parts.append(f"{total_critical} critical")
        if total_high:
            parts.append(f"{total_high} high")

        return SecuritySignal(
            status=status,
            source=source,
            observed_at=datetime.now(UTC),
            advisory_count=total_advisories,
            critical_count=total_critical,
            high_count=total_high,
            summary="; ".join(parts),
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_pip_audit(data: object) -> tuple[int, int, int]:
        """Parse pip-audit JSON (array of {name, version, vulns})."""
        if not isinstance(data, list):
            return 0, 0, 0

        advisory_count = 0
        critical = 0
        high = 0
        for entry in data:
            vulns = entry.get("vulns", []) if isinstance(entry, dict) else []
            advisory_count += len(vulns)
            for vuln in vulns:
                severity = (vuln.get("fix_versions") and "high") or "unknown"
                # pip-audit vulns don't have a standard severity field,
                # so we count every vuln as advisory and skip severity bucketing
                _ = severity
        return advisory_count, critical, high

    @staticmethod
    def _parse_npm_audit(data: object) -> tuple[int, int, int]:
        """Parse npm audit JSON (has ``vulnerabilities`` dict with severity)."""
        if not isinstance(data, dict):
            return 0, 0, 0

        vulns = data.get("vulnerabilities", {})
        if not isinstance(vulns, dict):
            return 0, 0, 0

        advisory_count = 0
        critical = 0
        high = 0
        for _name, info in vulns.items():
            if not isinstance(info, dict):
                continue
            advisory_count += 1
            sev = info.get("severity", "").lower()
            if sev == "critical":
                critical += 1
            elif sev == "high":
                high += 1

        return advisory_count, critical, high

    @staticmethod
    def _parse_trivy(data: object) -> tuple[int, int, int]:
        """Parse trivy JSON (has ``Results`` array with ``Vulnerabilities``)."""
        if not isinstance(data, dict):
            return 0, 0, 0

        results = data.get("Results", [])
        if not isinstance(results, list):
            return 0, 0, 0

        advisory_count = 0
        critical = 0
        high = 0
        for result in results:
            vulns = result.get("Vulnerabilities") or []
            for vuln in vulns:
                advisory_count += 1
                sev = vuln.get("Severity", "").upper()
                if sev == "CRITICAL":
                    critical += 1
                elif sev == "HIGH":
                    high += 1

        return advisory_count, critical, high
