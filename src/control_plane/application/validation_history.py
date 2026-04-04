"""Track validation failure signatures across runs for recurring-failure detection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from control_plane.domain.models import ValidationResult


class ValidationHistory:
    """Records and queries validation failure signatures across run directories."""

    def __init__(self, report_root: Path) -> None:
        self.report_root = report_root

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def compute_signatures(validation_results: list[ValidationResult]) -> list[tuple[str, str]]:
        """Return ``(command, md5_hex)`` for each *failed* result.

        The hash is computed from the normalized (stripped + lowercased)
        concatenation of stderr and stdout.
        """
        sigs: list[tuple[str, str]] = []
        for r in validation_results:
            if r.exit_code == 0:
                continue
            text = (r.stderr + r.stdout).strip().lower()
            digest = hashlib.md5(text.encode()).hexdigest()  # noqa: S324
            sigs.append((r.command, digest))
        return sigs

    def record_signatures(self, task_id: str, signatures: list[tuple[str, str]]) -> None:
        """Persist *signatures* into the most recent run directory for *task_id*."""
        run_dir = self._latest_run_dir(task_id)
        if run_dir is None:
            return
        path = run_dir / "validation_signatures.json"
        path.write_text(json.dumps(signatures, indent=2))

    def check_recurring(
        self,
        task_id: str,
        signatures: list[tuple[str, str]],
        window: int = 5,
        threshold: int = 2,
    ) -> bool:
        """Return ``True`` if any signature appears in >= *threshold* of the last *window* runs."""
        run_dirs = self._recent_run_dirs(task_id, window)
        if not run_dirs:
            return False

        # Collect signature sets per historical run
        historical_sets: list[set[tuple[str, str]]] = []
        for d in run_dirs:
            sig_file = d / "validation_signatures.json"
            if not sig_file.exists():
                continue
            try:
                data = json.loads(sig_file.read_text())
                historical_sets.append({tuple(s) for s in data})  # type: ignore[misc]
            except (json.JSONDecodeError, TypeError):
                continue

        if not historical_sets:
            return False

        current = {tuple(s) for s in signatures}
        for sig in current:
            count = sum(1 for hs in historical_sets if sig in hs)
            if count >= threshold:
                return True
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run_dirs_for_task(self, task_id: str) -> list[Path]:
        """Return all run directories matching *task_id*, sorted by name (timestamp)."""
        if not self.report_root.exists():
            return []
        dirs = sorted(
            d for d in self.report_root.iterdir() if d.is_dir() and f"_{task_id}_" in d.name
        )
        return dirs

    def _latest_run_dir(self, task_id: str) -> Path | None:
        dirs = self._run_dirs_for_task(task_id)
        return dirs[-1] if dirs else None

    def _recent_run_dirs(self, task_id: str, window: int) -> list[Path]:
        dirs = self._run_dirs_for_task(task_id)
        return dirs[-window:]
