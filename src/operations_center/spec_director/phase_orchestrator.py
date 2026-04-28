# src/operations_center/spec_director/phase_orchestrator.py
"""Phase orchestrator — advances spec campaign phases and unblocks stuck tasks."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from operations_center.spec_director._claude_cli import call_claude
from operations_center.spec_director.state import CampaignStateManager

logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset({"done", "cancelled"})


def _status(issue: dict) -> str:
    state = issue.get("state")
    if isinstance(state, dict):
        return str(state.get("name", "")).lower()
    return str(state or "").lower()


def _labels(issue: dict) -> list[str]:
    raw = issue.get("labels", [])
    result = []
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, dict):
                n = r.get("name")
                if n:
                    result.append(str(n))
            elif r:
                result.append(str(r))
    return result


def _campaign_id_from_issue(issue: dict) -> str | None:
    for lbl in _labels(issue):
        if lbl.lower().startswith("campaign-id:"):
            return lbl.split(":", 1)[1].strip()
    return None


def _has_lifecycle_label(issue: dict, value: str) -> bool:
    """Check for `lifecycle: <value>` label (e.g. lifecycle: expanded)."""
    target = f"lifecycle: {value}".lower()
    return any(lbl.strip().lower() == target for lbl in _labels(issue))


def _task_kind(issue: dict) -> str:
    for lbl in _labels(issue):
        if lbl.strip().lower().startswith("task-kind:"):
            return lbl.split(":", 1)[1].strip().lower()
    return "goal"


def _parse_rewrite_count(description: str) -> int:
    m = re.search(r"block_rewrite_count:\s*(\d+)", description)
    return int(m.group(1)) if m else 0


def _set_rewrite_count(description: str, count: int) -> str:
    if re.search(r"block_rewrite_count:\s*\d+", description):
        return re.sub(r"block_rewrite_count:\s*\d+", f"block_rewrite_count: {count}", description)
    # Inject after task_phase line if present
    updated = re.sub(
        r"(task_phase:\s*\S+)",
        rf"\1\nblock_rewrite_count: {count}",
        description,
        count=1,
    )
    if updated == description:
        # Fallback: inject at end of ## Execution section (before next ## or end of string)
        new_updated = re.sub(
            r"(## Execution\n(?:(?!##).)*?)(\n## |\Z)",
            lambda m: m.group(1) + f"\nblock_rewrite_count: {count}" + m.group(2),
            description,
            count=1,
            flags=re.DOTALL,
        )
        if new_updated != description:
            updated = new_updated
        else:
            # No ## Execution section at all — append at end
            updated = description.rstrip("\n") + f"\nblock_rewrite_count: {count}\n"
    return updated


def _read_spec_text(description: str, specs_dir: Path) -> str:
    m = re.search(r"spec_file:\s*(\S+)", description)
    if not m:
        return ""
    rel = m.group(1)
    candidates = [specs_dir / Path(rel).name, Path(rel)]
    for p in candidates:
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            continue
    return ""


@dataclass
class PhaseOrchestrationResult:
    phases_advanced: int = 0
    tasks_unblocked: int = 0
    tasks_cancelled: int = 0
    campaigns_completed: int = 0
    errors: list[str] = field(default_factory=list)


class PhaseOrchestrator:
    def __init__(
        self,
        client: Any,
        state_manager: CampaignStateManager,
        specs_dir: Path,
        max_rewrite_attempts: int = 2,
    ) -> None:
        self._client = client
        self._state = state_manager
        self._specs_dir = specs_dir
        self._max_rewrites = max_rewrite_attempts

    def run(self, issues: list[dict]) -> PhaseOrchestrationResult:
        result = PhaseOrchestrationResult()
        active = self._state.load()
        for campaign in active.active_campaigns():
            try:
                self._orchestrate(campaign.campaign_id, issues, result)
            except Exception as exc:
                logger.error(
                    '{"event": "phase_orchestrator_error", "campaign_id": "%s", "error": "%s"}',
                    campaign.campaign_id, str(exc),
                )
                result.errors.append(f"{campaign.campaign_id}: {exc}")
        return result

    def _orchestrate(
        self,
        campaign_id: str,
        issues: list[dict],
        result: PhaseOrchestrationResult,
    ) -> None:
        by_phase: dict[str, list[dict]] = {
            "goal": [],
            "test_campaign": [],
            "improve_campaign": [],
            "parent": [],
        }
        for issue in issues:
            if _campaign_id_from_issue(issue) != campaign_id:
                continue
            if str(issue.get("name", "")).startswith("[Campaign]"):
                by_phase["parent"].append(issue)
            else:
                kind = _task_kind(issue)
                bucket = kind if kind in by_phase else "goal"
                by_phase[bucket].append(issue)

        # Handle blocked tasks before phase-advancement check
        for phase_key in ("goal", "test_campaign", "improve_campaign"):
            for issue in by_phase[phase_key]:
                if _status(issue) == "blocked":
                    self._handle_blocked(issue, result)

        # Phase advancement is evaluated independently for each phase transition.
        # Both checks run every cycle: if goal and test_campaign tasks happen to be
        # terminal simultaneously, both transitions fire in the same cycle (fast-path).
        # The board is the ground truth; next cycle's state reflects actual outcomes.

        # NOTE: _handle_blocked transitions issues on the board, but the in-memory
        # `issues` list still reflects the old state for this cycle. Phase advancement
        # will see the updated state on the next cycle (correct — the task was re-queued).

        # Phase advancement: implement (goal) → test_campaign
        if by_phase["goal"] and self._all_terminal(by_phase["goal"]):
            backlog_test = [i for i in by_phase["test_campaign"] if _status(i) == "backlog"]
            for issue in backlog_test:
                self._client.transition_issue(str(issue["id"]), "Ready for AI")
                result.phases_advanced += 1
            if backlog_test:
                self._comment_parent(
                    by_phase["parent"],
                    f"Advancing to test phase: {len(backlog_test)} tasks promoted.",
                )
                logger.info(
                    '{"event": "phase_advanced", "campaign_id": "%s", "to": "test_campaign", "count": %d}',
                    campaign_id, len(backlog_test),
                )

        # Phase advancement: test_campaign → improve_campaign
        if by_phase["test_campaign"] and self._all_terminal(by_phase["test_campaign"]):
            backlog_improve = [i for i in by_phase["improve_campaign"] if _status(i) == "backlog"]
            for issue in backlog_improve:
                self._client.transition_issue(str(issue["id"]), "Ready for AI")
                result.phases_advanced += 1
            if backlog_improve:
                self._comment_parent(
                    by_phase["parent"],
                    f"Advancing to improve phase: {len(backlog_improve)} tasks promoted.",
                )
                logger.info(
                    '{"event": "phase_advanced", "campaign_id": "%s", "to": "improve_campaign", "count": %d}',
                    campaign_id, len(backlog_improve),
                )

        # Campaign completion: all child tasks terminal
        all_tasks = by_phase["goal"] + by_phase["test_campaign"] + by_phase["improve_campaign"]
        if all_tasks and self._all_terminal(all_tasks):
            done_n = sum(1 for i in all_tasks if _status(i) == "done")
            cancelled_n = sum(1 for i in all_tasks if _status(i) == "cancelled")
            for parent in by_phase["parent"]:
                self._client.transition_issue(str(parent["id"]), "Done")
                self._client.comment_issue(
                    str(parent["id"]),
                    f"Campaign complete. {done_n} tasks done, {cancelled_n} cancelled.",
                )
            self._state.mark_complete(campaign_id)
            result.campaigns_completed += 1
            logger.info(
                '{"event": "campaign_complete", "campaign_id": "%s", "done": %d, "cancelled": %d}',
                campaign_id, done_n, cancelled_n,
            )

    def _all_terminal(self, issues: list[dict]) -> bool:
        return bool(issues) and all(_status(i) in _TERMINAL_STATES for i in issues)

    def _handle_blocked(self, issue: dict, result: PhaseOrchestrationResult) -> None:
        task_id = str(issue["id"])

        # Lifecycle guard: a task carrying `lifecycle: expanded` has been
        # decomposed into children whose own runs do the real work. Don't
        # call kodo to rewrite its description — the parent is intentionally
        # quiescent and waiting for its children to finish (then the
        # board_worker auto-closes it). Without this guard the orchestrator
        # generates ghost rewrites against meta-tasks. See
        # docs/architecture/ghost_work_audit.md G12.
        if _has_lifecycle_label(issue, "expanded"):
            logger.info(
                '{"event": "blocked_rewrite_skipped", "task_id": "%s", "reason": "lifecycle_expanded"}',
                task_id,
            )
            return

        try:
            full = self._client.fetch_issue(task_id)
            description = str(
                full.get("description") or full.get("description_stripped") or ""
            )
        except Exception:
            description = str(
                issue.get("description") or issue.get("description_stripped") or ""
            )

        if len(description.strip()) < 20:
            logger.warning(
                '{"event": "blocked_rewrite_skipped", "task_id": "%s", "reason": "empty_description"}',
                task_id,
            )
            return

        # Fetch the most recent comment (failure comment left by kodo)
        last_comment_body: str | None = None
        try:
            comments = self._client.list_issue_comments(task_id)
            if comments:
                last_comment_body = str(comments[-1].get("body", "") or "")
        except Exception:
            logger.debug(
                '{"event": "blocked_comments_fetch_failed", "task_id": "%s"}',
                task_id,
            )

        rewrite_count = _parse_rewrite_count(description)
        if rewrite_count >= self._max_rewrites:
            cancel_reason = last_comment_body or "no reason available"
            self._client.transition_issue(task_id, "Cancelled")
            self._client.comment_issue(
                task_id,
                f"Task cancelled after {self._max_rewrites} rewrite attempts: {cancel_reason}",
            )
            result.tasks_cancelled += 1
            logger.info(
                '{"event": "blocked_task_cancelled", "task_id": "%s", "rewrites": %d}',
                task_id, rewrite_count,
            )
            return

        spec_text = _read_spec_text(description, self._specs_dir)
        title = str(issue.get("name", ""))
        prompt = (
            "Rewrite this Plane task description to be clearer and more actionable.\n"
            "Keep all ## section headers. Do NOT change repo:, base_branch:, mode:, "
            "spec_campaign_id:, spec_file:, task_phase: fields in ## Execution.\n"
            "Output ONLY the rewritten task description with no preamble.\n\n"
            f"## Task title\n{title}\n\n"
            f"## Current description\n{description}\n"
        )
        if spec_text:
            prompt += f"\n## Spec context (do not change the spec)\n{spec_text[:3000]}\n"
        if last_comment_body:
            prompt += f"\n## Failure comment\n{last_comment_body}\n"

        try:
            rewritten = call_claude(prompt)
        except Exception as exc:
            logger.warning(
                '{"event": "blocked_rewrite_failed", "task_id": "%s", "error": "%s"}',
                task_id, str(exc),
            )
            return

        new_count = rewrite_count + 1
        rewritten = _set_rewrite_count(rewritten, new_count)
        self._client.update_issue_description(task_id, rewritten)
        self._client.transition_issue(task_id, "Ready for AI")
        self._client.comment_issue(
            task_id,
            f"Description rewritten (attempt {new_count}/{self._max_rewrites}). Re-queued.",
        )
        result.tasks_unblocked += 1
        logger.info(
            '{"event": "blocked_task_unblocked", "task_id": "%s", "rewrite_count": %d}',
            task_id, new_count,
        )

    def _comment_parent(self, parents: list[dict], message: str) -> None:
        for parent in parents:
            try:
                self._client.comment_issue(str(parent["id"]), message)
            except Exception:
                pass
