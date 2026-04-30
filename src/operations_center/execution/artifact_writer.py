# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""execution/artifact_writer.py — persist canonical run contracts to disk.

Writes one directory per execution run:

    <root>/<run_id>/
        proposal.json
        decision.json
        execution_request.json
        result.json
        run_metadata.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from operations_center.contracts.execution import ExecutionRequest, ExecutionResult
from operations_center.contracts.proposal import TaskProposal
from operations_center.contracts.routing import LaneDecision

_DEFAULT_ROOT = Path.home() / ".console" / "operations_center" / "runs"


class RunArtifactWriter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _DEFAULT_ROOT

    def write_run(
        self,
        *,
        proposal: TaskProposal,
        decision: LaneDecision,
        request: ExecutionRequest,
        result: ExecutionResult,
        executed: bool,
        extra_metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        run_dir = self.root / result.run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        files = [
            (run_dir / "proposal.json", proposal.model_dump_json(indent=2)),
            (run_dir / "decision.json", decision.model_dump_json(indent=2)),
            (run_dir / "execution_request.json", request.model_dump_json(indent=2)),
            (run_dir / "result.json", result.model_dump_json(indent=2)),
        ]
        for path, content in files:
            path.write_text(content + "\n", encoding="utf-8")

        metadata: dict[str, Any] = {
            "run_id": result.run_id,
            "proposal_id": proposal.proposal_id,
            "decision_id": decision.decision_id,
            "selected_lane": decision.selected_lane.value,
            "selected_backend": decision.selected_backend.value,
            "status": result.status.value,
            "success": result.success,
            "executed": executed,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }
        if result.failure_category is not None:
            metadata["failure_category"] = result.failure_category.value
        if extra_metadata:
            metadata.update(extra_metadata)

        (run_dir / "run_metadata.json").write_text(
            json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8"
        )

        return [str(p) for p, _ in files] + [str(run_dir / "run_metadata.json")]

    def write_partial(
        self,
        *,
        run_id: str,
        proposal: TaskProposal | None = None,
        decision: LaneDecision | None = None,
        reason: str = "",
        extra_metadata: dict[str, Any] | None = None,
    ) -> list[str]:
        """Persist whatever contracts exist when execution fails before completion."""
        run_dir = self.root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        if proposal is not None:
            p = run_dir / "proposal.json"
            p.write_text(proposal.model_dump_json(indent=2) + "\n", encoding="utf-8")
            written.append(str(p))
        if decision is not None:
            p = run_dir / "decision.json"
            p.write_text(decision.model_dump_json(indent=2) + "\n", encoding="utf-8")
            written.append(str(p))

        metadata: dict[str, Any] = {
            "run_id": run_id,
            "partial": True,
            "reason": reason,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        m = run_dir / "run_metadata.json"
        m.write_text(json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8")
        written.append(str(m))

        return written
