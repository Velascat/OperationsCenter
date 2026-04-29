# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""Doc-convention detectors as a Custodian plugin contributor.

The C8/C9 detectors caught most documentation drift but their noise
floor was high because docs in this repo follow *several* conventions
(autonomy_gaps.md, managed_repo_*.md, ADRs, design specs, runtime
operator docs). DC1-DC5 enforce a baseline shape so future docs
follow consistent patterns and the C-series can stop adding bespoke
skip rules.

Five conventions:

  DC1  Design specs in docs/design/ have a YAML front matter block
       with at least ``status:`` set
  DC2  Cross-doc references (`docs/X.md`, `see X.md`) resolve to a
       file that actually exists
  DC3  ADRs live under docs/architecture/adr/ following NNNN-title.md
       naming (zero-padded, kebab-case)
  DC4  README has the conventional top-level sections (Overview,
       Quick start, Architecture)
  DC5  Backtick-quoted symbol citations in implementation contexts use
       fully-qualified names (module.func or class.method) so the
       reader doesn't have to grep — already partially enforced by C8
       skip-list, surfaced here as its own count for visibility

Each detector returns DetectorResult(count, samples). Samples are
formatted as ``path:line: message`` for CLI display. Status is "open"
where findings are expected (we have legacy docs predating these
conventions); future PRs raise the bar by either fixing or formally
exempting offenders.

Invariant compliance:
  • Read-only — operates entirely on .md files; no source-tree edits
  • No imports of behavior_calibration
  • No routing decisions
"""
from __future__ import annotations

import re
from pathlib import Path

from custodian.audit_kit.detector import AuditContext, Detector, DetectorResult


# ── DC1: design-spec front matter ────────────────────────────────────────────

def _detect_dc1_design_front_matter(ctx: AuditContext) -> DetectorResult:
    design_dir = ctx.repo_root / "docs" / "design"
    if not design_dir.exists():
        return DetectorResult(count=0, samples=[])
    samples: list[str] = []
    for md in sorted(design_dir.glob("*.md")):
        rel = md.relative_to(ctx.repo_root)
        try:
            text = md.read_text(errors="replace")
        except OSError:
            continue
        # Front matter must start at line 1 with `---` and contain status:
        if not text.startswith("---"):
            samples.append(f"{rel}:1: missing YAML front matter (`---` block at top)")
            continue
        try:
            end = text.index("---", 3)
        except ValueError:
            samples.append(f"{rel}:1: front matter has no closing `---`")
            continue
        front = text[3:end]
        if not re.search(r"^\s*status\s*:", front, re.MULTILINE):
            samples.append(f"{rel}:1: front matter present but `status:` field missing")
    return DetectorResult(count=len(samples), samples=samples[:8])


# ── DC2: cross-doc references resolve ────────────────────────────────────────

_DOC_REF_RE = re.compile(r"`(docs/[a-z0-9_/\-]+\.md)`")


def _detect_dc2_dead_doc_references(ctx: AuditContext) -> DetectorResult:
    docs_root = ctx.repo_root / "docs"
    readme    = ctx.repo_root / "README.md"
    files: list[Path] = [readme] if readme.exists() else []
    if docs_root.exists():
        files.extend(docs_root.rglob("*.md"))

    samples: list[str] = []
    for f in files:
        rel = f.relative_to(ctx.repo_root)
        if "/archive/" in str(rel) or "/history/" in str(rel):
            continue
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                for m in _DOC_REF_RE.finditer(line):
                    target = ctx.repo_root / m.group(1)
                    if not target.exists():
                        samples.append(f"{rel}:{i}: dead reference `{m.group(1)}`")
                        if len(samples) >= 16:
                            return DetectorResult(count=len(samples), samples=samples[:8])
        except OSError:
            continue
    return DetectorResult(count=len(samples), samples=samples[:8])


# ── DC3: ADR naming convention ───────────────────────────────────────────────

_ADR_NAME_RE = re.compile(r"^\d{4}-[a-z0-9]+(?:-[a-z0-9]+)*\.md$")


def _detect_dc3_adr_naming(ctx: AuditContext) -> DetectorResult:
    adr_dir = ctx.repo_root / "docs" / "architecture" / "adr"
    if not adr_dir.exists():
        return DetectorResult(count=0, samples=[])
    samples: list[str] = []
    for md in sorted(adr_dir.glob("*.md")):
        if md.name.lower() in {"readme.md", "template.md", "index.md"}:
            continue
        if not _ADR_NAME_RE.match(md.name):
            samples.append(f"{md.relative_to(ctx.repo_root)}: doesn't match NNNN-kebab-case.md")
    return DetectorResult(count=len(samples), samples=samples[:8])


# ── DC4: README section presence ─────────────────────────────────────────────

_REQUIRED_README_HEADINGS = (
    re.compile(r"^##\s+(?:Quick\s+start|Quickstart|Getting\s+started)\b", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^##\s+(?:Architecture|Overview|How\s+it\s+works)\b",     re.IGNORECASE | re.MULTILINE),
)


def _detect_dc4_readme_sections(ctx: AuditContext) -> DetectorResult:
    readme = ctx.repo_root / "README.md"
    if not readme.exists():
        return DetectorResult(count=1, samples=["README.md missing entirely"])
    try:
        text = readme.read_text(errors="replace")
    except OSError:
        return DetectorResult(count=0, samples=[])
    missing = []
    labels = ("Quick start / Getting started", "Architecture / Overview")
    for rx, label in zip(_REQUIRED_README_HEADINGS, labels):
        if not rx.search(text):
            missing.append(f"README.md: missing section ({label})")
    return DetectorResult(count=len(missing), samples=missing)


# ── DC5: bare-symbol citations in implementation contexts ───────────────────

_IMPL_CONTEXT_RE = re.compile(r"\*\*Files:\*\*|\bImplementation:", re.IGNORECASE)
_BARE_SYMBOL_RE = re.compile(r"`([_a-z][a-zA-Z0-9_]{4,})\(?\)?`")


def _detect_dc5_unqualified_symbol_citations(ctx: AuditContext) -> DetectorResult:
    """Symbols cited in **Files:** lines without a module-qualified path.

    Bare ``foo_bar`` in a Files: line is an audit smell — the reader has
    to grep to find it. ``module/path.py:foo_bar`` or ``module.foo_bar``
    is the convention. Reports occurrences where backtick'd symbols
    appear in implementation contexts but lack a separator (``.`` or
    ``:`` or ``/``) elsewhere in the line.
    """
    docs_root = ctx.repo_root / "docs"
    files: list[Path] = []
    for sub in ("design", "architecture"):
        d = docs_root / sub
        if d.exists():
            files.extend(d.rglob("*.md"))
    samples: list[str] = []
    for f in files:
        rel = f.relative_to(ctx.repo_root)
        if "/archive/" in str(rel) or "/history/" in str(rel):
            continue
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if not _IMPL_CONTEXT_RE.search(line):
                    continue
                # Need at least one backtick'd symbol AND no separator in the
                # line; if there's a `.` `:` or `/` in any backticked group,
                # the convention is being followed somewhere on the line.
                has_qualified = bool(re.search(r"`[^`]*[./:][^`]*`", line))
                if has_qualified:
                    continue
                if _BARE_SYMBOL_RE.search(line):
                    samples.append(f"{rel}:{i}: bare symbol citation in Files: line")
                    if len(samples) >= 12:
                        return DetectorResult(count=len(samples), samples=samples[:8])
        except OSError:
            continue
    return DetectorResult(count=len(samples), samples=samples[:8])


def build_oc_doc_convention_detectors() -> list[Detector]:
    """Custodian plugin contributor for OC's doc conventions."""
    return [
        Detector("DC1", "design specs missing YAML front matter / status",     "open", _detect_dc1_design_front_matter),
        Detector("DC2", "cross-doc references that don't resolve",             "open", _detect_dc2_dead_doc_references),
        Detector("DC3", "ADRs not following NNNN-kebab-case.md",               "open", _detect_dc3_adr_naming),
        Detector("DC4", "README missing required sections",                    "open", _detect_dc4_readme_sections),
        Detector("DC5", "bare symbol citations in **Files:** contexts",        "open", _detect_dc5_unqualified_symbol_citations),
    ]
