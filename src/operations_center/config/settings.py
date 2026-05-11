# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

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


class BackendCapSettings(BaseModel):
    """Per-backend execution cap and resource thresholds.

    Keyed on the backend that powers the dispatch (``kodo``, ``archon``,
    ``aider``, ``openclaw``, ``pi``, ...). All fields are optional;
    backends with no entry in ``Settings.backend_caps`` are
    unconstrained at this layer — the global cap still applies.

    **Rate caps**:
      - ``max_per_hour`` / ``max_per_day`` — checked via
        ``UsageStore.budget_decision_for_backend()`` *after* the global
        and per-repo caps pass.

    **Resource thresholds** (all backends share the host's RAM and
    process pool — calibrate to *aggregate footprint when dispatched
    on this host*, not protocol overhead. An Archon HTTP dispatch is
    cheap to send but the Archon container is on the same machine and
    its child processes consume the same RAM that kodo subprocess
    teams need):
      - ``min_available_memory_mb`` — pre-dispatch check that free RAM
        is at least this much (read from ``/proc/meminfo``). Refuses
        the dispatch when below.
      - ``max_concurrent`` — how many in-flight executions of this
        backend OC will allow at once. Counted as
        ``execution_started`` minus ``execution_finished`` events.

    Typical config::

        backend_caps:
          kodo:
            max_per_day: 50
            min_available_memory_mb: 6144   # subprocess team config
            max_concurrent: 1               # teams hate sharing
          archon:
            max_per_day: 5                  # trust-building rate cap
            min_available_memory_mb: 8192   # container baseline + SDK call
            max_concurrent: 4
          aider:
            min_available_memory_mb: 1024
            max_concurrent: 2
          pi:
            min_available_memory_mb: 16384  # local LLM weights
            max_concurrent: 1
    """

    # Rate caps (Option A)
    max_per_hour: int | None = None
    max_per_day: int | None = None
    # Resource thresholds (Option A follow-up)
    min_available_memory_mb: int | None = None
    max_concurrent: int | None = None


class ResourceGateSettings(BaseModel):
    """Global resource gate that runs before any per-backend cap.

    The gate exists to reserve host headroom for **co-tenant workloads**
    on the same machine — operator-defined background pipelines that
    cannot tolerate having OC dispatches drain the RAM/CPU budget out
    from under them. Per-backend caps (``BackendCapSettings``) are
    still useful, but they only protect against a single backend
    stampeding; a mix of small dispatches across many backends can
    still push the box past what the co-tenants need to make forward
    progress.

    Both fields are optional; an empty ``resource_gate:`` block means
    "no global gate" and only per-backend caps fire.

    - ``max_concurrent`` — total in-flight OC dispatches across **all**
      backends. Counted as ``execution_started`` minus
      ``execution_finished`` events with no backend filter.
    - ``min_available_memory_mb`` — pre-dispatch check that free RAM
      (read from ``/proc/meminfo``) is at least this much, regardless
      of which backend is dispatching.

    Typical config (calibrated for a host that shares its CPU/RAM with
    a heavy background pipeline; leaves 12 GiB and at most 6
    concurrent OC runs free for the co-tenant)::

        resource_gate:
          max_concurrent: 6
          min_available_memory_mb: 12288   # reserve 12 GiB for co-tenants
    """

    max_concurrent: int | None = None
    min_available_memory_mb: int | None = None


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

    Composition order is platform → private → (project XOR work_scope) → local. The
    platform base is always the bundled ``platform_manifest.yaml`` shipped
    by the ``platform-manifest`` package. The optional second layer is:

    - ``private_manifest_path``: a PrivateManifest private topology superset
      owned outside the public PlatformManifest repo.

    The next layer is exactly one of:

    - ``project_manifest_path``: a single ProjectManifest describing one
      project unit.
    - ``work_scope_manifest_path``: a WorkScopeManifest composing multiple
      ProjectManifests via explicit ``includes:`` (PM v0.9.0+).

    Setting both is a configuration error. ``local_manifest_path`` layers
    on top of the chosen stack.

    All fields default to None; the loader returns the platform-only graph
    when nothing is configured. Set ``enabled=False`` to skip graph
    construction entirely.
    """

    enabled: bool = True
    project_slug: str | None = None
    private_manifest_path: Path | None = None
    project_manifest_path: Path | None = None
    work_scope_manifest_path: Path | None = None
    local_manifest_path: Path | None = None

    @model_validator(mode="after")
    def _project_xor_work_scope(self) -> "PlatformManifestSettings":
        if self.project_manifest_path is not None and self.work_scope_manifest_path is not None:
            raise ValueError(
                "platform_manifest: 'project_manifest_path' and "
                "'work_scope_manifest_path' are mutually exclusive — "
                "set exactly one. Use project_manifest_path for a single "
                "project; use work_scope_manifest_path for a multi-project "
                "OC work scope (PM v0.9.0+)."
            )
        return self


class _PropagationPairOverride(BaseModel):
    """One operator-authored (target, consumer) policy override."""

    target_repo_id: str
    consumer_repo_id: str
    action: str  # "skip" | "backlog" | "ready_for_ai"
    reason: str = "operator override"


class ContractChangePropagationSettings(BaseModel):
    """Configuration for the cross-repo task chaining engine (R5).

    Disabled by default. Operators flip ``enabled`` and choose which
    edge types auto-trigger downstream tasks. See
    docs/operator/manifest_wiring.md for the full operator runbook.
    """

    enabled: bool = False
    auto_trigger_edge_types: list[str] = Field(default_factory=list)
    dedup_window_hours: int = 24
    pair_overrides: list[_PropagationPairOverride] = Field(default_factory=list)
    # Where PropagationRecord artifacts land; relative paths resolve
    # against the OC repo root at runtime.
    record_dir: Path = Path("state/propagation")
    dedup_path: Path = Path("state/propagation/dedup.json")


class Settings(BaseModel):
    plane: PlaneSettings
    git: GitSettings
    kodo: KodoSettings
    archon: ArchonSettings = Field(default_factory=ArchonSettings)
    platform_manifest: PlatformManifestSettings = Field(default_factory=PlatformManifestSettings)
    contract_change_propagation: ContractChangePropagationSettings = Field(
        default_factory=ContractChangePropagationSettings
    )
    aider: AiderSettings = Field(default_factory=AiderSettings)
    aider_local: AiderLocalSettings = Field(default_factory=AiderLocalSettings)
    # Per-backend hourly/daily caps. Empty by default (no per-backend cap;
    # global cap still applies). See BackendCapSettings docstring.
    backend_caps: dict[str, BackendCapSettings] = Field(default_factory=dict)
    # Global resource gate. Runs *before* per-backend caps and reserves
    # host headroom for co-tenant workloads sharing the box. Empty by
    # default (no gate). See ResourceGateSettings docstring.
    resource_gate: ResourceGateSettings = Field(default_factory=ResourceGateSettings)
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


def _resolve_manifest_path(value: Path | None, config_dir: Path) -> Path | None:
    """Resolve a manifest path that may be ``~``-prefixed or relative.

    Absolute paths pass through unchanged. ``~`` is expanded against the
    invoking user's home. Relative paths resolve against the config-file
    directory (matches the kodo.binary resolution pattern).
    """
    if value is None:
        return None
    expanded = Path(str(value)).expanduser()
    if expanded.is_absolute():
        return expanded
    return (config_dir / expanded).resolve()


def _slugify_repo_key(key: str) -> str:
    """``OperationsCenter`` → ``operationscenter``; ``my_repo`` → ``my-repo``."""
    return key.lower().replace("_", "-")


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    settings = Settings.model_validate(raw)
    config_dir = config_path.parent
    settings.kodo.binary = _resolve_binary(settings.kodo.binary, config_dir)
    for profile in settings.kodo_profiles.values():
        profile.binary = _resolve_binary(profile.binary, config_dir)
    # Resolve platform_manifest paths relative to the config file dir so
    # operators can write `project_manifest_path: ../ExampleManagedRepo/topology/...`
    # without hardcoding absolute paths.
    pm = settings.platform_manifest
    pm.private_manifest_path = _resolve_manifest_path(pm.private_manifest_path, config_dir)
    pm.project_manifest_path = _resolve_manifest_path(pm.project_manifest_path, config_dir)
    pm.work_scope_manifest_path = _resolve_manifest_path(pm.work_scope_manifest_path, config_dir)
    pm.local_manifest_path = _resolve_manifest_path(pm.local_manifest_path, config_dir)
    # Auto-resolve project_slug from self_repo_key when unset.
    if pm.project_slug is None and settings.self_repo_key:
        pm.project_slug = _slugify_repo_key(settings.self_repo_key)
    return settings
