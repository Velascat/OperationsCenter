from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from control_plane.execution.models import ExecutionControlSettings


class PlaneSettings(BaseModel):
    base_url: str
    api_token_env: str
    workspace_slug: str
    project_id: str


class GitSettings(BaseModel):
    provider: str = "github"
    token_env: str | None = None
    # TODO: not yet wired — no runtime code reads this
    open_pr_default: bool = True
    push_on_validation_failure: bool = True
    author_name: str = "Control Plane Bot"
    author_email: str = "control-plane-bot@example.com"
    sign_commits: bool = False
    signing_key: str | None = None


class KodoSettings(BaseModel):
    binary: str = "kodo"
    team: str = "full"
    cycles: int = 3
    exchanges: int = 20
    orchestrator: str = "codex:gpt-5.4"
    effort: str = "standard"
    timeout_seconds: int = 3600


class EscalationSettings(BaseModel):
    webhook_url: str = ""
    # Number of same-classification blocks within 24h before escalating
    block_threshold: int = 5
    # Minimum seconds between two escalation POSTs for the same classification
    cooldown_seconds: int = 3600
    # S7-2: Warn when a GitHub token expires within this many days (0 = disabled)
    credential_expiry_warn_days: int = 7


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
    compliance_model: str = "claude-sonnet-4-6"
    drop_file_path: str = "state/spec_direction.md"
    max_active_campaigns: int = 1
    max_tasks_per_campaign: int = 6
    spec_retention_days: int = 90
    brainstorm_context_snapshot_kb: int = 8
    compliance_diff_max_kb: int = 32
    campaign_abandon_hours: int = 72


class ScheduledTask(BaseModel):
    cron: str  # e.g. "0 9 * * 1" (Monday 09:00 UTC)
    title: str
    goal: str
    repo_key: str
    kind: str = "goal"


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
    # HTML marker appended to every bot-posted comment — belt-and-suspenders filter
    bot_comment_marker: str = "<!-- controlplane:bot -->"
    # When True, autonomy-sourced PRs are merged automatically once CI is green
    # without waiting for a human 👍.  Only applies to tasks labelled
    # "source: autonomy".  Requires repo-level auto_merge_on_ci_green = True too.
    auto_merge_success_rate_threshold: float = 0.9


class RepoSettings(BaseModel):
    clone_url: str
    default_branch: str
    validation_commands: list[str] = Field(default_factory=list)
    allowed_base_branches: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    bootstrap_enabled: bool = True
    python_binary: str = "python3"
    venv_dir: str = ".venv"
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


class Settings(BaseModel):
    plane: PlaneSettings
    git: GitSettings
    kodo: KodoSettings
    repos: dict[str, RepoSettings]
    reviewer: ReviewerSettings = Field(default_factory=ReviewerSettings)
    report_root: Path = Path("tools/report/kodo_plane")
    # Keywords/phrases the proposer uses to prioritise proposals.  Proposals
    # whose title or goal text match any entry are kept at their natural
    # confidence; those that don't match are demoted to Backlog so the system
    # works on what matters before filling the board with lower-priority noise.
    # Leave empty (the default) to disable filtering.
    focus_areas: list[str] = Field(default_factory=list)
    # The repo key that identifies this ControlPlane installation itself.
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
    # ControlPlane does not parse Kodo billing output.
    cost_per_execution_usd: float = 0.0
    # Number of parallel task-execution slots per watcher lane.  1 = serial
    # (default).  Values > 1 launch that many threads that each poll and execute
    # tasks independently.  Periodic scans (heartbeat, merge-conflict, etc.)
    # only run in slot 0 to avoid duplicate work.
    parallel_slots: int = 1
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
    # Maximum number of concurrent kodo processes across all worker roles.
    # Workers that run kodo (goal, test) will skip their execute cycle when this
    # many kodo-shim processes are already running, then retry next poll.
    # 0 = unlimited (default 1).
    max_concurrent_kodo: int = 1
    # Minimum available system RAM in MB before a worker will launch kodo.
    # If available memory drops below this threshold the execute cycle is
    # skipped and a watch_skip_low_memory event is logged.  0 = disabled.
    min_kodo_available_mb: int = 400
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
    raw = yaml.safe_load(config_path.read_text())
    settings = Settings.model_validate(raw)
    config_dir = config_path.parent
    settings.kodo.binary = _resolve_binary(settings.kodo.binary, config_dir)
    for profile in settings.kodo_profiles.values():
        profile.binary = _resolve_binary(profile.binary, config_dir)
    return settings
