# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""OperationsCenter plugin detectors for Custodian.

Remaining OC-specific detectors (cannot be expressed by native Custodian config):

  OC3  Orphaned entrypoints  — module namespaces under operations_center.entrypoints.*
                               that are never imported or referenced anywhere outside
                               their own directory. OC-specific: knows the entrypoints/
                               directory contract.

  OC8  Doc phantom symbols   — K1 equivalent but also matches field-definition syntax
                               (``name: TypeAnnotation``) in addition to ``def``/``class``,
                               reducing false positives for DTO field references in docs.
                               Migrates fully once K1 gains field-def awareness.

  OC10 kodo max_concurrent must be 1 — reads config/operations_center.local.yaml (if
                               present) and confirms backend_caps.kodo.max_concurrent == 1.
                               Prevents inadvertent concurrency widening from the watchdog
                               loop or autonomy-cycle. Silently passes when the local config
                               is absent (CI / fresh clone).

Superseded and removed (native Custodian covers them):
  OC1  → U1–U3 (stub/unimplemented detector family)
  OC2  → C1 (deferred-aware TODO; domain-path exclusions in audit.exclude_paths.C1)
  OC4  → RUFF adapter
  OC5  → T3 (unconditional skips; OC hints in audit.t3_env_gate_hints)
  OC6  → dead placeholder (removed)
  OC7  → F3 (Pydantic BaseModel field liveness)
  OC9  → K2 (doc value drift; OC known_values in audit.known_values)
"""
from __future__ import annotations

import re
from pathlib import Path

from custodian.audit_kit.detector import AuditContext, Detector, DetectorResult, MEDIUM, LOW


# ── helpers ──────────────────────────────────────────────────────────────────

def _py_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


# ── OC3: orphaned entrypoints ─────────────────────────────────────────────────

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


# ── OC8: doc phantom symbols (field-def aware) ────────────────────────────────

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


# ── OC10: kodo max_concurrent must be 1 ───────────────────────────────────────

def _detect_oc10_kodo_max_concurrent(ctx: AuditContext) -> DetectorResult:
    """Confirm backend_caps.kodo.max_concurrent == 1 in the local config.

    Silently passes when the local config is absent (CI / fresh clone) so this
    never blocks the test suite on machines that haven't run setup.
    """
    import yaml as _yaml  # optional dep — only installed in dev venv

    local_cfg = ctx.repo_root / "config" / "operations_center.local.yaml"
    if not local_cfg.exists():
        return DetectorResult(count=0, samples=[])
    try:
        data = _yaml.safe_load(local_cfg.read_text())
    except Exception:
        return DetectorResult(count=0, samples=[])

    actual = (
        (data or {})
        .get("backend_caps", {})
        .get("kodo", {})
        .get("max_concurrent")
    )
    if actual is None:
        # Key absent — not a violation; may be inheriting default.
        return DetectorResult(count=0, samples=[])
    if actual != 1:
        return DetectorResult(
            count=1,
            samples=[
                f"config/operations_center.local.yaml: "
                f"backend_caps.kodo.max_concurrent={actual!r} — must be 1 "
                f"(watchdog loop invariant; concurrent kodo teams fight for host RAM)"
            ],
        )
    return DetectorResult(count=0, samples=[])


# ── contributor entry point ───────────────────────────────────────────────────

def build_oc_detectors() -> list[Detector]:
    return [
        Detector("OC3",  "orphaned entrypoints",                             "open", _detect_oc3_orphaned_entrypoints,  MEDIUM),
        Detector("OC8",  "docs reference a symbol that doesn't exist",       "open", _detect_oc8_phantom_symbols,       LOW),
        Detector("OC10", "kodo max_concurrent must be 1",                    "open", _detect_oc10_kodo_max_concurrent,  MEDIUM),
    ]
