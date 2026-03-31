from __future__ import annotations

import fnmatch
from pathlib import Path


class ChangedFilePolicyChecker:
    def find_violations(self, changed_files: list[str], allowed_paths: list[str]) -> list[str]:
        if not allowed_paths:
            return []

        normalized_patterns = [self._normalize_pattern(path) for path in allowed_paths]
        violations: list[str] = []
        for changed in changed_files:
            normalized_changed = self._normalize_changed_path(changed)
            if not any(fnmatch.fnmatch(normalized_changed, pattern) for pattern in normalized_patterns):
                violations.append(normalized_changed)
        return sorted(set(violations))

    @staticmethod
    def _normalize_pattern(path: str) -> str:
        cleaned = path.strip().replace("\\", "/").lstrip("./")
        if cleaned.endswith("/"):
            return f"{cleaned}*"
        return str(Path(cleaned)).replace("\\", "/")

    @staticmethod
    def _normalize_changed_path(path: str) -> str:
        normalized = path.strip()
        if " -> " in normalized:
            normalized = normalized.split(" -> ", maxsplit=1)[1]
        return str(Path(normalized)).replace("\\", "/").lstrip("./")
