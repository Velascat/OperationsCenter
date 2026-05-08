# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from operations_center.execution.models import ExecutionControlSettings


class PlaneSettings(BaseModel):
    base_url: str
    api_token_env: str
    workspace_slug: str
    project_id: str


class GitSettings(BaseModel):
    token_env: str | None = None
    open_pr_default: bool = True
    push_on_validation_failure: bool = True
    author_name: str = "Operations Center Bot"
    author_email: str = "operations-center-bot@example.com"
    sign_commits: bool = False
    signing_key: str | None = None


class ArchonSettings(BaseModel):
    """Settings for the Archon HTTP workflow backend.

    Archon is deployed by WorkStation (``compose/profiles/archon.yml``)
    at ``http://localhost:3000`` by default. ``workflow_names`` maps OC
    workflow_type values to actual Archon workflow names operators have
    shipped under ``.archon/workflows/``.
    """

    enabled: bool = False
    base_url: str = "http://localhost:3000"
    poll_interval_seconds: float = 2.0
    workflow_names: dict[str, str] = Field(
        default_factory=lambda: {
            "goal":    "archon-assist",
            "fix_pr":  "archon-fix-github-issue",
            "test":    "archon-test-loop-dag",
            "improve": "archon-refactor-safely",
        },
    )


class KodoSettings(BaseModel):
    binary: str = "kodo"
    team: str = "full"
    cycles: int = 3
    exchanges: int = 20
    orchestrator: str = "codex:gpt-5.4"
    effort: str = "standard"
    timeout_seconds: int = 3600


class AiderSettings(BaseModel):
    # Absolute path to the aider binary, e.g.
    # /home/dev/Documents/GitHub/SwitchBoard/.venv-aider/bin/aider
    binary: str = "aider"
    # Model prefix sent to SwitchBoard, combined as "<prefix>/<profile>"
    model_prefix: str = "openai"
    # Default SwitchBoard routing profile for Aider tasks
    profile: str = "capable"
    timeout_seconds: int = 3600
    # Optional path to aider model-settings YAML (from SwitchBoard repo)
    model_settings_file: str = ""
    extra_args: list[str] = Field(default_factory=list)


class AiderLocalSettings(BaseModel):
    binary: str = "aider"
    model: str = "ollama/qwen2.5-coder:3b"
    ollama_base_url: str = "http://localhost:11434"
    timeout_seconds: int = 1800
    extra_args: list[str] = Field(default_factory=list)


class EscalationSettings(BaseModel):
    webhook_url: str = ""
    # Minimum seconds between two escalation POSTs for the same classification
    cooldown_seconds: int = 3600


class ErrorIngestLogSource(BaseModel):
    """A log file to tail for ERROR lines and convert to Plane tasks."""
    path: str
    repo_key: str
    # Regex pattern that must match the line; default catches lines with ERROR or CRITICAL
    pattern: str = r"(ERROR|CRITICAL)"
    # Minimum seconds between tasks created for the same pattern match (dedup window)
    dedup_window_seconds: int = 3600


class ErrorIngestSettings(BaseModel):
    """Configuration for the runtime error ingestion service (S8-8)."""
    # Port for the HTTP webhook receiver (0 = disabled)
    webhook_port: int = 0
    # Log files to tail for error lines
    log_sources: list[ErrorIngestLogSource] = Field(default_factory=list)
    # Default repo_key for webhook events that don't specify one
    default_repo_key: str = ""


class SpecDirectorSettings(BaseModel):
    enabled: bool = True
    poll_interval_seconds: int = 120
    brainstorm_model: str = "claude-opus-4-6"
    drop_file_path: str = "state/spec_direction.md"
    max_tasks_per_campaign: int = 6
    spec_retention_days: int = 90
    campaign_abandon_hours: int = 72
    # Historical compatibility field retained only so old configs still load.
    # Spec director no longer injects routing environment variables at runtime.
    switchboard_url: str | None = None


class ScheduledTask(BaseModel):
    """A periodically-injected Plane task (e.g. weekly dependency audit).

    The propose cycle checks each entry; due tasks are created as Ready for
    AI and flow through the normal pipeline. This generates the *Plane work
    item*; it does NOT schedule the autonomy_cycle itself (that runs
    continuously).
    """
    # Base interval. Format: ``<num><unit>`` where unit ∈ {m,h,d,w}.
    # Examples: "30m", "6h", "1d", "1w". Required.
    every: str
    title: str
    goal: str
    repo_key: str
    kind: str = "goal"
    # Optional anchor: only fire when current UTC time matches HH:MM (within
    # the propose-cycle polling slack). If unset, fires whenever `every`
    # elapses regardless of time of day.
    at: str | None = None
    # Optional weekday gate (lowercase 3-letter abbrev: mon/tue/wed/.../sun).
    # Empty / None means any day.
    on_days: list[str] | None = None


class MaintenanceWindow(BaseModel):
    """A recurring time window during which autonomous execution is paused.

    ``start_hour`` and ``end_hour`` are in UTC (0–23, exclusive end).
    If ``start_hour`` > ``end_hour`` the window wraps midnight.
    ``days`` is a list of weekday numbers (0=Monday … 6=Sunday);
    empty list means the window applies every day.

    Example — suspend all execution from 02:00–04:00 UTC on weekdays:
        start_hour: 2
        end_hour: 4
        days: [0, 1, 2, 3, 4]
    """
    start_hour: int   # 0–23
    end_hour: int     # 0–23 (exclusive); wrap allowed (start > end)
    days: list[int] = Field(default_factory=list)  # empty = all days


class ReviewerSettings(BaseModel):
    # GitHub logins whose comments are always ignored (bots, CI accounts)
    bot_logins: list[str] = Field(default_factory=list)
    # If non-empty, only comments from these logins trigger human revisions
    allowed_reviewer_logins: list[str] = Field(default_factory=list)
    # Max kodo self-review+revision cycles before escalating to human
    max_self_review_loops: int = 2
    # Max kodo revision passes driven by human comments before auto-merging
    max_human_review_loops: int = 3
    # Seconds from phase-2 entry before auto-merging (default: 1 day)
    human_review_timeout_seconds: int = 86400
    # HTML marker appended to every bot-posted comment — belt-and-suspenders filter
    bot_comment_marker: str = "<!-- operations-center:bot -->"


class RepoSettings(BaseModel):
    clone_url: str
    default_branch: str
    # When set, autonomy-generated PRs target this branch instead of
    # default_branch. Useful while building trust in the loop: work
    # accumulates on a sandbox branch (e.g. "autonomy-staging") and a human
    # cherry-picks or merges to main when ready. None = autonomy targets
    # default_branch directly. Only applies to autonomy / spec-campaign /
    # board_worker sources; reviewer self-review and operator-launched runs
    # ignore this.
    sandbox_base_branch: str | None = None
    validation_commands: list[str] = Field(default_factory=list)
    allowed_base_branches: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    bootstrap_enabled: bool = True
    python_binary: str = "python3"
    venv_dir: str | None = ".venv"
    install_dev_command: str | None = None
    token_env: str | None = None
    await_review: bool = False
    propose_enabled: bool = True
    local_path: str | None = None
    bootstrap_commands: list[str] | None = None  # custom bootstrap (replaces Python venv setup for non-Python repos)
    validation_timeout_seconds: int = 300
    # Per-repo daily execution cap (None = no per-repo limit, global budget applies).
    # Use this to prevent one repo from exhausting the full day's budget.
    max_daily_executions: int | None = None
    # When True and the task is source: autonomy, the review watcher merges the PR
    # automatically once CI is green without waiting for a human 👍.
    auto_merge_on_ci_green: bool = False
    # S7-6: Paths in this repo that are shared interfaces across repos.
    # When any of these paths are touched by an execution, a cross-repo impact
    # warning is added to the task comment so operators can check sibling repos.
    impact_report_paths: list[str] = Field(default_factory=list)
    # S8-9: When True, the review watcher will never auto-merge on timeout.
    # The PR must receive an explicit 👍 or human approval comment.
    require_explicit_approval: bool = False
    # When True, baseline validation is skipped entirely.  Use for repos with
    # pre-existing widespread violations not caused by any single task, to avoid
    # an endless fix-validation task loop.  Post-execution validation still runs.
    skip_baseline_validation: bool = False
    # CI check names to ignore when deciding whether CI is passing.  Use for
    # pre-existing failures on the base branch that are unrelated to PR changes
    # (e.g. a file-tag linter that was broken before the PR landed).  Checks
    # whose names contain any of these strings are excluded from the failed list.
    ci_ignored_checks: list[str] = Field(default_factory=list)
    # Phase 6 — executor selection. ``"kodo"`` (default) or ``"aider"``.
    executor: str = "kodo"


class PlatformManifestSettings(BaseModel):
    """Configuration for the EffectiveRepoGraph composition pipeline.

    Composition order is platform → project → local. The platform base is
    always the bundled ``platform_manifest.yaml`` shipped by the
    ``platform-manifest`` package. ``project_manifest_path`` and
    ``local_manifest_path`` layer on top per the PlatformManifest design.

    All fields default to None; the loader returns the platform-only graph
    when nothing project- or local-specific is configured. Set
    ``enabled=False`` to skip graph construction entirely.
    """

    enabled: bool = True
    project_slug: str | None = None
    project_manifest_path: Path | None = None
    local_manifest_path: Path | None = None


class Settings(BaseModel):
    plane: PlaneSettings
    git: GitSettings
    kodo: KodoSettings
    archon: ArchonSettings = Field(default_factory=ArchonSettings)
    platform_manifest: PlatformManifestSettings = Field(default_factory=PlatformManifestSettings)
    aider: AiderSettings = Field(default_factory=AiderSettings)
    aider_local: AiderLocalSettings = Field(default_factory=AiderLocalSettings)
    repos: dict[str, RepoSettings]
    reviewer: ReviewerSettings = Field(default_factory=ReviewerSettings)
    report_root: Path = Path("tools/report/kodo_plane")
    # The repo key that identifies this OperationsCenter installation itself.
    # Tasks targeting this repo require a "self-modify: approved" label before
    # the goal/test watcher will auto-execute them, and proposals for it are
    # always placed in Backlog rather than Ready for AI.
    self_repo_key: str | None = None
    escalation: EscalationSettings = Field(default_factory=EscalationSettings)
    scheduled_tasks: list[ScheduledTask] = Field(default_factory=list)
    # Number of days a PR can remain open without activity before stale-PR scan
    # closes it and requeues the task.
    stale_pr_days: int = 7
    # Estimated USD cost per Kodo execution for spend telemetry.  Set to 0.0
    # (the default) to disable cost recording.  The value is operator-supplied;
    # OperationsCenter does not parse Kodo billing output.
    cost_per_execution_usd: float = 0.0
    # Per-task-kind Kodo execution profile overrides.  Keys are task_kind values
    # (e.g. "goal", "improve", "test") or a special "default" fallback.  Any
    # field omitted in a profile inherits from the top-level ``kodo`` block.
    # Example:
    #   kodo_profiles:
    #     lint_fix:           # task created with task-kind: goal + source_family: lint_fix
    #       cycles: 2
    #       effort: low
    #     context_limit:
    #       cycles: 6
    #       exchanges: 40
    #       effort: high
    kodo_profiles: dict[str, KodoSettings] = Field(default_factory=dict)
    # Recurring time windows during which proposal creation and task execution
    # are suppressed.  Use this to prevent autonomous activity during planned
    # deploy windows, maintenance periods, or overnight freezes.
    maintenance_windows: list[MaintenanceWindow] = Field(default_factory=list)
    # S8-3: Days before a source:autonomy Backlog task is considered stale and
    # eligible for cancellation.  0 = disabled.
    stale_autonomy_backlog_days: int = 30
    # S8-8: Runtime error ingestion configuration.  None = disabled.
    error_ingest: ErrorIngestSettings | None = None
    spec_director: SpecDirectorSettings = Field(default_factory=SpecDirectorSettings)
    # Propose worker skips its generation cycle when the "Ready for AI" queue
    # already has this many or more tasks.  0 = disabled (default 8).
    propose_skip_when_ready_count: int = 8

    def plane_token(self) -> str:
        return os.environ[self.plane.api_token_env]

    def git_token(self) -> str | None:
        if self.git.token_env is None:
            return None
        return os.environ.get(self.git.token_env)

    def repo_git_token(self, repo_key: str) -> str | None:
        repo = self.repos.get(repo_key)
        if repo and repo.token_env:
            return os.environ.get(repo.token_env)
        return self.git_token()

    def execution_controls(self) -> ExecutionControlSettings:
        return ExecutionControlSettings.from_env()


def _resolve_binary(binary: str, config_dir: Path) -> str:
    """Resolve a relative binary path to an absolute one.

    Tries config-file directory first, then falls back to cwd (the project
    root when the process starts), so paths like ``scripts/kodo-shim`` work
    even when the config lives in a subdirectory (e.g. ``config/``).
    """
    if not binary or Path(binary).is_absolute():
        return binary
    for base in (config_dir, Path.cwd()):
        resolved = (base / binary).resolve()
        if resolved.exists():
            return str(resolved)
    return binary


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    settings = Settings.model_validate(raw)
    config_dir = config_path.parent
    settings.kodo.binary = _resolve_binary(settings.kodo.binary, config_dir)
    for profile in settings.kodo_profiles.values():
        profile.binary = _resolve_binary(profile.binary, config_dir)
    return settings
