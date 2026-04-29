# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""OperationsCenter plugin detectors for Custodian.

OC's nine code-health detectors, namespaced ``OC1``-``OC9`` to avoid
collision with Custodian's generic ``C1``-``C8`` defaults. Custodian's
generic detectors (TODO/print/bare-except/etc.) run alongside; the
combined audit reports both sets.

Each detector here is a port of the body that previously lived in
``src/operations_center/entrypoints/code_health_audit/main.py``. The
adapter shape change is minimal — Custodian's ``AuditContext`` exposes
the same ``src_root`` / ``tests_root`` / ``repo_root`` triple OC's
detectors expected; only the wrapping into Custodian's ``Detector`` /
``DetectorResult`` types is new.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from custodian.audit_kit.detector import AuditContext, Detector, DetectorResult


# ── helpers ──────────────────────────────────────────────────────────────────

def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def _grep_lines(root: Path, pattern: str, *, max_results: int = 400) -> list[tuple[Path, int, str]]:
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


# ── OC1: scaffolded-but-unimplemented backends ───────────────────────────────

def _detect_oc1_stub_backends(ctx: AuditContext) -> DetectorResult:
    samples = []
    for f in _py_files(ctx.src_root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if "raise NotImplementedError" in text and "/backends/" in str(f):
            rel = f.relative_to(ctx.repo_root)
            samples.append(str(rel))
    return DetectorResult(count=len(samples), samples=samples[:10])


# ── OC2: untagged TODO/FIXME debt ────────────────────────────────────────────

_REVIEWED_TAG = re.compile(r"\[deferred,\s*reviewed\b", re.IGNORECASE)


def _detect_oc2_untagged_todos(ctx: AuditContext) -> DetectorResult:
    todo_domain_paths = (
        "observer/collectors/todo_signal",
        "observer/artifact_writer",
        "decision/rules/todo_accumulation",
        "proposer/candidate_mapper",
        "insights/derivers/quality_trend",
    )
    hits = _grep_lines(ctx.src_root, r"\b(TODO|FIXME|XXX|HACK)\b")
    samples: list[str] = []
    for f, lineno, line in hits:
        rel = f.relative_to(ctx.repo_root)
        if any(p in str(rel) for p in todo_domain_paths):
            continue
        if _REVIEWED_TAG.search(line):
            continue
        if "tests/" in str(rel):
            continue
        samples.append(f"{rel}:{lineno}: {line.strip()[:80]}")
    return DetectorResult(count=len(samples), samples=samples[:8])


# ── OC3: orphaned entrypoints ────────────────────────────────────────────────

def _detect_oc3_orphaned_entrypoints(ctx: AuditContext) -> DetectorResult:
    ep_root = ctx.src_root / "entrypoints"
    if not ep_root.is_dir():
        return DetectorResult(count=0, samples=[])
    samples: list[str] = []
    for sub in sorted(ep_root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("_"):
            continue
        ref_count = 0
        rx = f"operations_center.entrypoints.{sub.name}"
        for root in (ctx.src_root, ctx.tests_root, ctx.repo_root / "scripts", ctx.repo_root / "docs"):
            if not root.exists():
                continue
            for f in root.rglob("*"):
                if not f.is_file() or "__pycache__" in f.parts:
                    continue
                if str(f.relative_to(ctx.repo_root)).startswith(f"src/operations_center/entrypoints/{sub.name}"):
                    continue
                try:
                    if rx in f.read_text(errors="replace"):
                        ref_count += 1
                        break
                except OSError:
                    continue
            if ref_count:
                break
        pyproj = ctx.repo_root / "pyproject.toml"
        if pyproj.exists():
            try:
                if rx in pyproj.read_text():
                    ref_count += 1
            except OSError:
                pass
        if ref_count == 0:
            samples.append(f"entrypoints/{sub.name}/")
    return DetectorResult(count=len(samples), samples=samples[:10])


# ── OC4: ruff lint findings ──────────────────────────────────────────────────

def _detect_oc4_ruff(ctx: AuditContext) -> DetectorResult:
    try:
        proc = subprocess.run(
            ["ruff", "check", str(ctx.src_root), "--output-format", "concise"],
            capture_output=True, text=True, cwd=ctx.repo_root,
        )
    except FileNotFoundError:
        return DetectorResult(count=0, samples=["# ruff not installed"])
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    findings = [line for line in lines if ":" in line and ("error" not in line.lower() or "[" in line)]
    findings = [f for f in findings if not f.startswith("Found ") and not f.startswith("All ")]
    return DetectorResult(count=len(findings), samples=findings[:8])


# ── OC5: unconditional skipped tests ─────────────────────────────────────────

_ENV_GATE_HINTS = ("aider", "switchboard", "OPERATIONS_CENTER_", "shutil.which", "pkg_path", "not present")


def _detect_oc5_unconditional_skips(ctx: AuditContext) -> DetectorResult:
    samples: list[str] = []
    for f in _py_files(ctx.tests_root):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            is_call = "pytest.skip(" in line
            is_decorator = stripped.startswith("@pytest.mark.skip")
            if not is_call and not is_decorator:
                continue
            window = "\n".join(text.splitlines()[max(0, i - 6): i + 1])
            if any(h.lower() in window.lower() for h in _ENV_GATE_HINTS):
                continue
            samples.append(f"{f.relative_to(ctx.repo_root)}:{i}: {line.strip()[:80]}")
    return DetectorResult(count=len(samples), samples=samples[:8])


# ── OC6: modules called only from tests (placeholder) ────────────────────────

def _detect_oc6_test_only_modules(ctx: AuditContext) -> DetectorResult:
    return DetectorResult(count=0, samples=[])


# ── OC7: dead settings fields ────────────────────────────────────────────────

def _detect_oc7_dead_settings(ctx: AuditContext) -> DetectorResult:
    settings_file = ctx.src_root / "config" / "settings.py"
    if not settings_file.exists():
        return DetectorResult(count=0, samples=[])
    try:
        text = settings_file.read_text()
    except OSError:
        return DetectorResult(count=0, samples=[])
    field_re = re.compile(r"^\s+([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z]", re.MULTILINE)
    fields = set(field_re.findall(text))
    ignored = {"version", "name", "type", "config", "id", "model_config"}
    fields -= ignored
    samples: list[str] = []
    for fname in sorted(fields):
        rx_dot     = re.compile(rf"\.{fname}\b")
        rx_idx     = re.compile(rf"""\[['\"]{fname}['\"]\]""")
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
    return DetectorResult(count=len(samples), samples=samples[:10])


# ── OC8: doc phantom symbols ─────────────────────────────────────────────────

def _detect_oc8_phantom_symbols(ctx: AuditContext) -> DetectorResult:
    docs_root = ctx.repo_root / "docs"
    readme    = ctx.repo_root / "README.md"
    files: list[Path] = [readme] if readme.exists() else []
    for sub in ("design", "architecture"):
        d = docs_root / sub
        if d.exists():
            files.extend(d.rglob("*.md"))

    sym_re = re.compile(r"`(_?[a-z][a-z0-9_]{7,})`")
    impl_marker_re = re.compile(
        r"\*\*Files:\*\*|\bImplementation:|see\s+`|defined in `|"
        r"\b(?:def|class)\s+|`\s*\(.*?\)\s*",
        re.IGNORECASE,
    )
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

    audit_cfg = ctx.config.get("audit", {}) or {}
    common_words = set(audit_cfg.get("common_words", []) or [])
    stale_handlers = set(audit_cfg.get("stale_handlers", []) or [])

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
        if re.search(rf'"event"\s*:\s*"{re.escape(name)}"', src_text):
            return True
        return False

    deferred_words = ("deferred", "out of scope", "not yet implemented", "future:", "deprecated")
    seen: dict[str, tuple[Path, int]] = {}
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(ctx.repo_root))
        if "/history/" in rel or "/audits/" in rel or "/archive/" in rel:
            continue
        current_section_deferred = False
        for i, line in enumerate(text.splitlines(), 1):
            lower = line.lower()
            if line.startswith("#"):
                current_section_deferred = any(w in lower for w in deferred_words)
                continue
            if current_section_deferred:
                continue
            if any(w in lower for w in deferred_words):
                continue
            if not impl_marker_re.search(line):
                continue
            if value_context_re.search(line):
                continue
            for m in sym_re.finditer(line):
                name = m.group(1)
                if name in seen or name in stale_handlers or _exists(name):
                    continue
                seen[name] = (f, i)

    samples = [
        f"{path.relative_to(ctx.repo_root)}:{ln}: `{name}` referenced but no def/class"
        for name, (path, ln) in sorted(seen.items())
    ]
    return DetectorResult(count=len(seen), samples=samples[:8])


# ── OC9: doc value drift ─────────────────────────────────────────────────────

def _detect_oc9_doc_value_drift(ctx: AuditContext) -> DetectorResult:
    docs_root = ctx.repo_root / "docs"
    readme    = ctx.repo_root / "README.md"
    files: list[Path] = [readme] if readme.exists() else []
    if docs_root.exists():
        files.extend(docs_root.rglob("*.md"))

    src_text = ""
    for f in _py_files(ctx.src_root):
        try:
            src_text += f.read_text(errors="replace") + "\n"
        except OSError:
            continue

    audit_cfg = ctx.config.get("audit", {}) or {}
    extra_known = set(audit_cfg.get("known_values", []) or [])
    known_values = {
        "ready for ai", "in review", "in progress", "backlog", "done",
        "cancelled", "blocked", "running", "awaiting input",
        "lgtm", "concerns", "approved", "rejected",
        "low", "medium", "high", "urgent", "none",
        "small", "medium", "large",
        "goal", "test", "improve", "review", "spec",
        "test_campaign", "improve_campaign",
        "info", "warn", "warning", "error", "critical",
        "bool", "int", "str", "list", "dict", "tuple", "float", "bytes",
        "fcntl", "subprocess", "logging", "pathlib", "datetime",
    } | {v.lower() for v in extra_known}

    value_line_re = re.compile(
        r"(?:status|state|kind|value|priority|severity|level|verdict|outcome)s?\s*"
        r"(?:[:=]|\bcan be\b|\bis\b|\bof\b)",
        re.IGNORECASE,
    )
    sym_re = re.compile(r"`([a-z][a-z0-9_]{2,})`")
    seen: dict[str, tuple[Path, int]] = {}
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = str(f.relative_to(ctx.repo_root))
        if "/history/" in rel or "/audits/" in rel or "/archive/" in rel:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not value_line_re.search(line):
                continue
            for m in sym_re.finditer(line):
                name = m.group(1)
                if name in seen:
                    continue
                if name.lower() in known_values:
                    continue
                if re.search(rf"""['"]{re.escape(name)}['"]""", src_text):
                    continue
                if re.search(rf"^\s+{re.escape(name)}\s*:\s*[A-Za-z]", src_text, re.MULTILINE):
                    continue
                if re.search(rf"\b(def|class)\s+{re.escape(name)}\b", src_text):
                    continue
                seen[name] = (f, i)
    samples = [
        f"{path.relative_to(ctx.repo_root)}:{ln}: `{name}` cited as a value but no string-literal in src/"
        for name, (path, ln) in sorted(seen.items())
    ]
    return DetectorResult(count=len(seen), samples=samples[:8])


# ── contributor entry point ──────────────────────────────────────────────────

def build_oc_detectors() -> list[Detector]:
    """Custodian plugin contributor for OperationsCenter.

    Custodian's generic ``C1``-``C8`` (TODO/print/bare-except/etc.) run by
    default; these ``OC1``-``OC9`` are the OC-specific overlays that the
    generic set doesn't cover.
    """
    return [
        Detector("OC1", "scaffolded-but-unimplemented backends",       "open",    _detect_oc1_stub_backends),
        Detector("OC2", "untagged TODO/FIXME debt (deferred-aware)",   "open",    _detect_oc2_untagged_todos),
        Detector("OC3", "orphaned entrypoints",                        "open",    _detect_oc3_orphaned_entrypoints),
        Detector("OC4", "ruff lint findings",                          "fixed",   _detect_oc4_ruff),
        Detector("OC5", "unconditional skipped tests",                 "open",    _detect_oc5_unconditional_skips),
        Detector("OC6", "modules called only from tests",              "deferred", _detect_oc6_test_only_modules),
        Detector("OC7", "dead settings fields",                        "open",    _detect_oc7_dead_settings),
        Detector("OC8", "docs reference a symbol that doesn't exist",  "open",    _detect_oc8_phantom_symbols),
        Detector("OC9", "docs cite a value not in src as a string literal", "open", _detect_oc9_doc_value_drift),
    ]
