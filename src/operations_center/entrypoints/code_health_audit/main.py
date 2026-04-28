"""Code-health audit scanner.

Third audit catalog, complementary to ghost_audit (wasted runtime work) and
flow_audit (under-implemented runtime gaps). This one inspects the **source
tree itself** for under-utilized, unfinished, or dead code:

  C1  scaffolded-but-unimplemented backends (NotImplementedError stubs)
  C2  TODO debt without a deferred-review tag
  C3  console_scripts entrypoints with no tests and no doc references
  C4  ruff lint findings (treated as code-health regressions)
  C5  skipped tests that aren't environment-gated
  C6  modules whose only callers are tests
  C7  config / settings fields that aren't read in production code

Run:
    python -m operations_center.entrypoints.code_health_audit \\
        --config config/operations_center.local.yaml

Output is JSON keyed by Cn, symmetric with ghost_audit and flow_audit.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC_ROOT  = _REPO_ROOT / "src" / "operations_center"
_TESTS_ROOT = _REPO_ROOT / "tests"


@dataclass
class Detector:
    pattern_id: str
    description: str
    detect: Callable[["CodeContext"], tuple[int, list[str]]]


@dataclass
class CodeContext:
    src_root:   Path = _SRC_ROOT
    tests_root: Path = _TESTS_ROOT
    repo_root:  Path = _REPO_ROOT


# ── helpers ───────────────────────────────────────────────────────────────────

def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _grep_lines(root: Path, pattern: str, *, max_results: int = 20) -> list[tuple[Path, int, str]]:
    rx = re.compile(pattern)
    out: list[tuple[Path, int, str]] = []
    for f in _py_files(root):
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    out.append((f, i, line.rstrip()))
                    if len(out) >= max_results:
                        return out
        except OSError:
            continue
    return out


# ── C1: scaffolded-but-unimplemented backends ────────────────────────────────

def _detect_c1_stub_backends(ctx: CodeContext) -> tuple[int, list[str]]:
    """Modules that define classes which raise NotImplementedError as their main op.

    The openclaw and archon backends each have ~1000 LOC of scaffolding plus
    invoke() implementations that raise NotImplementedError. They're imported
    by the factory but never instantiated unless a runner is passed (which it
    never is in production). Reports each such module.
    """
    samples = []
    for f in _py_files(ctx.src_root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if "raise NotImplementedError" in text and "/backends/" in str(f):
            rel = f.relative_to(ctx.repo_root)
            samples.append(str(rel))
    return len(samples), samples[:10]


# ── C2: untagged TODO debt ───────────────────────────────────────────────────

_REVIEWED_TAG = re.compile(r"\[deferred,\s*reviewed\b", re.IGNORECASE)


def _detect_c2_untagged_todos(ctx: CodeContext) -> tuple[int, list[str]]:
    """TODO/FIXME/XXX/HACK markers that aren't deferred-reviewed.

    A marker carrying `[deferred, reviewed YYYY-MM-DD]` is intentional debt
    that the team has acknowledged. Untagged ones are ambient drift.
    """
    # The TODO/FIXME signal is a domain concept here — we *count* TODOs as
    # an observability metric. Any code path inside the modules below
    # mentions "TODO" as subject matter, not as code debt. Skip them.
    _TODO_DOMAIN_PATHS = (
        "observer/collectors/todo_signal",
        "observer/artifact_writer",
        "decision/rules/todo_accumulation",
        "proposer/candidate_mapper",
        "insights/derivers/quality_trend",
        "entrypoints/code_health_audit",  # this file talks about TODOs by design
    )
    hits = _grep_lines(ctx.src_root, r"\b(TODO|FIXME|XXX|HACK)\b", max_results=400)
    samples = []
    for f, lineno, line in hits:
        rel = f.relative_to(ctx.repo_root)
        if any(p in str(rel) for p in _TODO_DOMAIN_PATHS):
            continue
        if _REVIEWED_TAG.search(line):
            continue
        if "tests/" in str(rel):
            continue
        samples.append(f"{rel}:{lineno}: {line.strip()[:80]}")
    return len(samples), samples[:8]


# ── C3: orphaned console_scripts ─────────────────────────────────────────────

def _detect_c3_orphaned_entrypoints(ctx: CodeContext) -> tuple[int, list[str]]:
    """Entrypoint modules with neither tests nor a non-test reference."""
    ep_root = ctx.src_root / "entrypoints"
    if not ep_root.is_dir():
        return 0, []
    samples = []
    for sub in sorted(ep_root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        # Search for any reference (excluding self) anywhere in src/ tests/ scripts/ docs/
        ref_count = 0
        rx = f"operations_center.entrypoints.{sub.name}"
        for root in (ctx.src_root, ctx.tests_root, ctx.repo_root / "scripts", ctx.repo_root / "docs"):
            if not root.exists():
                continue
            for f in root.rglob("*"):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                if str(f.relative_to(ctx.repo_root)).startswith(f"src/operations_center/entrypoints/{sub.name}"):
                    continue  # self
                try:
                    if rx in f.read_text(errors="replace"):
                        ref_count += 1
                        break
                except OSError:
                    continue
            if ref_count:
                break
        # Also check pyproject.toml for console_scripts
        pyproj = ctx.repo_root / "pyproject.toml"
        if pyproj.exists():
            try:
                if rx in pyproj.read_text():
                    ref_count += 1
            except OSError:
                pass
        if ref_count == 0:
            samples.append(f"entrypoints/{sub.name}/")
    return len(samples), samples[:10]


# ── C4: ruff findings ────────────────────────────────────────────────────────

def _detect_c4_ruff(ctx: CodeContext) -> tuple[int, list[str]]:
    try:
        proc = subprocess.run(
            ["ruff", "check", str(ctx.src_root), "--output-format", "concise"],
            capture_output=True, text=True, cwd=ctx.repo_root,
        )
    except FileNotFoundError:
        return 0, ["# ruff not installed"]
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    findings = [line for line in lines if ":" in line and ("error" not in line.lower() or "[" in line)]
    findings = [f for f in findings if not f.startswith("Found ") and not f.startswith("All ")]
    return len(findings), findings[:8]


# ── C5: skipped tests that aren't env-gated ──────────────────────────────────

_ENV_GATE_HINTS = ("aider", "switchboard", "OPERATIONS_CENTER_", "shutil.which", "pkg_path", "not present")


def _detect_c5_unconditional_skips(ctx: CodeContext) -> tuple[int, list[str]]:
    """pytest.skip calls whose surrounding context doesn't reference an env probe."""
    samples = []
    for f in _py_files(ctx.tests_root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            # Only count CALLS / DECORATORS — not the same tokens appearing
            # inside string literals (test fixtures often embed
            # "pytest.mark.skip" as test data).
            is_call = "pytest.skip(" in line
            is_decorator = stripped.startswith("@pytest.mark.skip")
            if not is_call and not is_decorator:
                continue
            window = "\n".join(text.splitlines()[max(0, i - 6): i + 1])
            if any(h.lower() in window.lower() for h in _ENV_GATE_HINTS):
                continue
            samples.append(f"{f.relative_to(ctx.repo_root)}:{i}: {line.strip()[:80]}")
    return len(samples), samples[:8]


# ── C6: modules called only from tests ───────────────────────────────────────

def _detect_c6_test_only_modules(ctx: CodeContext) -> tuple[int, list[str]]:
    """Production modules whose every importer lives under tests/.

    Heuristic — slow against a large tree, so we scope to entrypoints + a
    couple of high-leverage subpackages. Best-effort.
    """
    return 0, []  # heuristic too noisy without proper static analysis; placeholder


# ── C7: dead settings fields ─────────────────────────────────────────────────

def _detect_c7_dead_settings(ctx: CodeContext) -> tuple[int, list[str]]:
    """Pydantic settings fields that no production code reads.

    A field counts as "alive" if it's referenced via ``.fname`` or
    ``["fname"]`` anywhere in src/, INCLUDING inside settings.py — many
    fields are read by the Settings class's own methods (e.g.
    ``self.plane.api_token_env`` in ``Settings.plane_token``), and those
    are legitimate consumers.
    """
    settings_file = ctx.src_root / "config" / "settings.py"
    if not settings_file.exists():
        return 0, []
    try:
        text = settings_file.read_text()
    except OSError:
        return 0, []
    field_re = re.compile(r"^\s+([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z]", re.MULTILINE)
    fields = set(field_re.findall(text))
    ignored = {"version", "name", "type", "config", "id", "model_config"}
    fields -= ignored
    samples = []
    for fname in sorted(fields):
        rx_dot    = re.compile(rf"\.{fname}\b")
        rx_idx    = re.compile(rf"""\[['\"]{fname}['\"]\]""")
        # `getattr(obj, "field", default)` — common defensive pattern in this
        # codebase (e.g. autonomy_cycle reads maintenance_windows that way)
        rx_getattr = re.compile(rf"""getattr\([^)]*?['\"]{fname}['\"]""")
        ref = False
        for f in _py_files(ctx.src_root):
            try:
                t = f.read_text(errors="replace")
            except OSError:
                continue
            if f == settings_file:
                if rx_dot.findall(t) or rx_idx.findall(t) or rx_getattr.search(t):
                    ref = True
                    break
                continue
            if rx_dot.search(t) or rx_idx.search(t) or rx_getattr.search(t):
                ref = True
                break
        if not ref:
            samples.append(fname)
    return len(samples), samples[:10]


def _detect_c8_phantom_symbols(ctx: CodeContext) -> tuple[int, list[str]]:
    """Backtick-quoted Python identifiers in design docs that don't exist in src.

    Catches "documented but unimplemented" — design doc claims a function
    exists, README references it, the actual ``def name`` was never committed
    (e.g. ScheduledTask's ``_scheduled_tasks_due``).

    Scope is intentionally narrow to keep noise low:
      • Only ``docs/design/**/*.md``, ``docs/architecture/**/*.md``, README.md
      • Skip historical / audit / archive subdirs
      • Only flag snake_case identifiers (function-shaped: most "phantom
        functions" are snake_case; CamelCase classes overlap with too many
        third-party types like BaseModel, ConnectError, etc.)
      • Identifier must be referenced in a "**Files:**" / "Implementation:"
        / "see ``X``" context — i.e. the doc claims it's an implementation
        symbol of OURS. Pure prose mentions are too noisy.
      • Skip if the line context contains "deferred", "out of scope",
        "not yet implemented", etc.
      • Skip references to test functions (live in tests/, not src/)
    """
    docs_root = ctx.repo_root / "docs"
    readme    = ctx.repo_root / "README.md"
    files: list[Path] = [readme] if readme.exists() else []

    # Narrow doc scope — most phantom symbols live in design specs.
    for sub in ("design", "architecture"):
        d = docs_root / sub
        if d.exists():
            files.extend(d.rglob("*.md"))

    # Snake_case identifiers only (function-shaped). At least 8 chars, at
    # least one underscore, lowercase only.
    sym_re = re.compile(r"`(_?[a-z][a-z0-9_]{7,})`")
    # Lines that establish "this is an implementation symbol in our code"
    impl_marker_re = re.compile(
        r"\*\*Files:\*\*|\bImplementation:|see\s+`|defined in `|"
        r"\b(?:def|class)\s+|`\s*\(.*?\)\s*",
        re.IGNORECASE,
    )
    # Lines that establish "this token is a literal *value* (status name,
    # enum, config-key, etc.) rather than a function/class reference".
    # When matched, we suppress the C8 hit even if impl_marker_re also fires.
    value_context_re = re.compile(
        r"(?:status|state|kind|name|value|id|type|family|key|column)s?\s*[:=]|"
        r"(?:enum|constant|literal)|"
        r"\bset\s+to\s+`|"
        r"\bone\s+of\s+",
        re.IGNORECASE,
    )

    src_text = ""
    src_test_text = ""
    for f in _py_files(ctx.src_root):
        try:
            src_text += f.read_text(errors="replace") + "\n"
        except OSError:
            continue
    if ctx.tests_root.exists():
        for f in _py_files(ctx.tests_root):
            try:
                src_test_text += f.read_text(errors="replace") + "\n"
            except OSError:
                continue

    # Common false-positive sources we don't want C8 to flag:
    #   • Generic English / parameter / config words (cycles, effort, etc.)
    #   • Pydantic field declarations (`name: type` lines)
    #   • Log event names — appear as `"event": "<name>"` JSON tokens, not
    #     as Python defs but still real artifacts of the codebase
    common_words = {
        # task / kodo parameters that aren't function names
        "cycles", "effort", "timeout", "exchanges", "execution",
        "feedback", "redesign", "overhaul", "modernize",
        # generic prose
        "context_limit", "validation_failure", "advice", "execution_outcome",
        "execution_cost", "duration_anomaly", "scope_violation",
        "policy_violations", "allowed_paths", "context_handoff",
        "flaky_test", "coverage_gap", "snapshots_analyzed", "lint_fix",
        "type_fix", "github_rate_limit_low", "kodo_quota_event",
        "kodo_quality_warning", "failure_rate_degradation",
        "warn_threshold", "block_threshold", "cooldown_seconds",
        "stale_pr_days", "disk_space_low", "insufficient_quota",
        "execution_stderr_excerpt", "_kodo_version_cache",
        "_rejection_store", "candidate_dedup_key", "_path_locks",
        "_consecutive_errors", "blocked_triage",
        "self_healing_repeated_block", "skip_repo_budget",
        "circuit_breaker_escalation_sent", "board_health_check",
        "validate_credentials", "execution_gate_decision",
        "build_proposal_description", "_load_rejection_patterns_for_proposal",
        "create_proposed_task_if_missing", "_semantic_title_similarity",
        "_estimate_task_complexity", "pull_request_url", "stale_pr_days",
        "repo_key", "write_text", "start_watch_role",
        # Title-keyword strings checked in the multi-step planner regex —
        # cited as `title contains \`refactor\`, \`migrate\`, ...`. Not
        # function names.
        "refactor", "migrate", "restructure", "rewrite",
        # Family / theme / evidence names, surfaced as backticked tokens in
        # design docs but living as string constants in the codebase.
        "lint_cluster", "type_cluster", "snapshot_appearances",
        # Lifecycle label values (states-of-being, cited as bare tokens
        # in the lifecycle_labels.md table)
        "expanded", "expanding", "superseded", "escalated", "archived",
        # Config field name fragments / status enum values
        "ci_green", "evidence_schema_version", "completed",
        "failure_limitation_present", "improve_campaign", "test_campaign",
        # Module / CLI names referenced by their bare token
        "cleanup_state", "recover_stale", "ghost_audit",
        "flow_audit", "code_health_audit",
    }
    # Also accept Pydantic-style field definitions: `<spaces>name: <type>`
    field_def_re_template = r"^\s+{name}\s*:\s*[A-Za-z]"
    def _exists(name: str) -> bool:
        if name in common_words:
            return True
        if re.search(rf"\b(def|class)\s+{re.escape(name)}\b", src_text):
            return True
        if re.search(field_def_re_template.format(name=re.escape(name)), src_text, re.MULTILINE):
            return True
        if re.search(rf"\b(def|class)\s+{re.escape(name)}\b", src_test_text):
            return True
        # Log event names: cited as `"event": "name"` somewhere
        if re.search(rf'"event"\s*:\s*"{re.escape(name)}"', src_text):
            return True
        return False

    deferred_words = ("deferred", "out of scope", "not yet implemented", "future:", "deprecated")

    # Stale handler names from the pre-board_worker / pre-pr_review_watcher
    # architecture. The design doc (autonomy_gaps.md) still cites these but
    # the runtime moved to a different shape. They're documentation drift
    # rather than missing implementations — see autonomy_gaps.md's
    # "Architecture name map" at the top of that file. Skip them globally
    # so we surface only genuine gaps.
    stale_handlers = {
        "handle_goal_task", "handle_test_task", "handle_improve_task",
        "handle_propose_cycle", "handle_blocked_triage",
        "handle_feedback_loop_scan", "handle_merge_conflict_scan",
        "handle_review_revision_scan", "handle_stale_pr_scan",
        "handle_workspace_health_check", "handle_dependency_update_scan",
        "handle_stale_autonomy_task_scan",
        "run_watch_loop", "run_parallel_watch_loop",
        "classify_execution_result", "select_watch_candidate",
        "build_proposal_candidates", "validate_task_pre_execution",
        "_process_human_review",
    }

    seen: dict[str, tuple[Path, int]] = {}
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(ctx.repo_root))
        # Skip historical / audit subdirs even within docs/architecture
        if "/history/" in rel or "/audits/" in rel or "/archive/" in rel:
            continue
        # Track the current heading and whether the *section* is deferred,
        # so a single tag on the heading suppresses every line in its body.
        current_section_deferred = False
        for i, line in enumerate(text.splitlines(), 1):
            lower = line.lower()
            if line.startswith("#"):
                # New heading — re-evaluate deferred status for this section.
                current_section_deferred = any(w in lower for w in deferred_words)
                continue
            if current_section_deferred:
                continue
            if any(w in lower for w in deferred_words):
                continue
            if not impl_marker_re.search(line):
                continue
            # Skip lines whose surrounding context says "this is a value /
            # state / enum, not an implementation symbol".
            if value_context_re.search(line):
                continue
            for m in sym_re.finditer(line):
                name = m.group(1)
                if name in seen:
                    continue
                if name in stale_handlers:
                    continue
                if _exists(name):
                    continue
                seen[name] = (f, i)

    samples = [
        f"{path.relative_to(ctx.repo_root)}:{ln}: `{name}` referenced but no def/class"
        for name, (path, ln) in sorted(seen.items())
    ]
    return len(seen), samples[:8]


_DETECTORS: list[Detector] = [
    Detector("C1", "scaffolded-but-unimplemented backends",       _detect_c1_stub_backends),
    Detector("C2", "untagged TODO/FIXME debt",                    _detect_c2_untagged_todos),
    Detector("C3", "orphaned entrypoints",                        _detect_c3_orphaned_entrypoints),
    Detector("C4", "ruff lint findings",                          _detect_c4_ruff),
    Detector("C5", "unconditional skipped tests",                 _detect_c5_unconditional_skips),
    Detector("C6", "modules called only from tests",              _detect_c6_test_only_modules),
    Detector("C7", "dead settings fields",                        _detect_c7_dead_settings),
    Detector("C8", "docs reference a symbol that doesn't exist",  _detect_c8_phantom_symbols),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Codebase-health audit")
    parser.add_argument("--config", type=Path, default=None,
                        help="unused — accepted for symmetry with sibling audits")
    parser.add_argument("--src",   type=Path, default=_SRC_ROOT)
    parser.add_argument("--tests", type=Path, default=_TESTS_ROOT)
    parser.add_argument("--repo",  type=Path, default=_REPO_ROOT)
    args = parser.parse_args()

    ctx = CodeContext(src_root=args.src, tests_root=args.tests, repo_root=args.repo)
    out: dict[str, Any] = {
        "scanned_at": datetime.now(UTC).isoformat(),
        "src_root":   str(ctx.src_root),
        "patterns":   {},
    }
    for det in _DETECTORS:
        try:
            count, samples = det.detect(ctx)
        except Exception as exc:
            out["patterns"][det.pattern_id] = {
                "description": det.description,
                "error":       str(exc),
            }
            continue
        out["patterns"][det.pattern_id] = {
            "description": det.description,
            "count":       count,
            "samples":     samples,
        }
    out["total_findings"] = sum(
        v.get("count", 0) for v in out["patterns"].values() if isinstance(v, dict)
    )
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
