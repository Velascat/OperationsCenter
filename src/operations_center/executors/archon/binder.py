# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""R3 — RuntimeBinding → .archon/config.yaml templater (closes G-001 in code).

Per the spike (2026-05-05): Archon's per-workflow LLM provider/model can
be set via ``.archon/config.yaml`` inside the worktree. Inheritance:
node → workflow → repo .archon/config.yaml → ~/.archon/config.yaml → env.

This binder writes the per-invocation config into the worktree before
``archon workflow run``. Worktree isolation makes it race-free across
concurrent invocations.

Translation table:
  kind=cli_subscription, provider=anthropic, model=opus    → provider: anthropic, model: claude-opus-4
  kind=cli_subscription, provider=anthropic, model=sonnet  → provider: anthropic, model: claude-sonnet-4
  kind=hosted_api, provider=anthropic, model=...           → provider: anthropic, model: <as-given>
  kind=hosted_api, provider=openai, model=...              → provider: openai, model: <as-given>
  kind=backend_default                                     → no config written

Anything else returns ``BindError`` for the adapter to surface as a
failure result.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from operations_center.contracts.execution import RuntimeBindingSummary

# Verified against real Archon source 2026-05-05 (ProtocolWarden/Archon @ fa6fc46f):
#   - provider literals on the wire: 'claude' | 'codex' (NOT 'anthropic'/'openai')
#   - claude models: 'opus' | 'sonnet' | 'haiku' | 'claude-*' | 'inherit' (verbatim)
#   - workflow YAML uses bare model names directly — no '-4' aliasing needed
#
# Earlier versions of this binder rewrote opus → claude-opus-4 and used
# provider: anthropic. Both wrong; Archon's loader rejected the result.
# See Archon G-004 for the discovery trail.

# CxRP RuntimeBinding.provider → Archon's provider literal.
# RuntimeBinding uses industry-standard provider names; Archon uses its
# own short labels. This is the conversion table.
_PROVIDER_TO_ARCHON = {
    "anthropic": "claude",
    "openai":    "codex",
}


class BindError(ValueError):
    """Raised when a RuntimeBinding cannot be templated to Archon config."""


@dataclass(frozen=True)
class ArchonConfigSelection:
    """The result of binding a RuntimeBinding to an Archon config."""
    config_yaml: Optional[dict]   # None means "use Archon's default"
    label: str
    provider: Optional[str] = None
    model: Optional[str] = None


def _archon_provider(rb_provider: str) -> Optional[str]:
    """Map a CxRP RuntimeBinding provider name to Archon's literal."""
    return _PROVIDER_TO_ARCHON.get(rb_provider)


def bind(rb: Optional[RuntimeBindingSummary]) -> ArchonConfigSelection:
    if rb is None or rb.kind == "backend_default":
        return ArchonConfigSelection(config_yaml=None, label="archon_default")

    if rb.kind == "cli_subscription":
        rb_provider = (rb.provider or "anthropic").lower()
        archon_provider = _archon_provider(rb_provider)
        if archon_provider != "claude":
            raise BindError(
                f"cli_subscription only supports provider=anthropic for Archon today; "
                f"got provider={rb.provider!r}"
            )
        model = (rb.model or "sonnet").lower()  # 'opus'|'sonnet'|'haiku' verbatim
        cfg = {"provider": archon_provider, "model": model}
        return ArchonConfigSelection(
            config_yaml=cfg, label=f"cli_subscription_{model}",
            provider=archon_provider, model=model,
        )

    if rb.kind == "hosted_api":
        rb_provider = (rb.provider or "").lower()
        archon_provider = _archon_provider(rb_provider)
        if not archon_provider:
            raise BindError(
                f"hosted_api binding requires a known provider "
                f"(anthropic|openai); got {rb.provider!r}"
            )
        if not rb.model:
            raise BindError("hosted_api binding requires model")
        model = rb.model.lower()  # passed verbatim — Archon validates per-provider
        cfg = {"provider": archon_provider, "model": model}
        if rb.endpoint:
            cfg["base_url"] = rb.endpoint
        return ArchonConfigSelection(
            config_yaml=cfg, label=f"hosted_{archon_provider}_{model}",
            provider=archon_provider, model=model,
        )

    raise BindError(
        f"Archon binder has no rule for kind={rb.kind!r}. "
        "Update archon/runtime_support.yaml or extend this binder."
    )


def write_worktree_config(
    workspace_path: Path,
    selection: ArchonConfigSelection,
) -> Optional[Path]:
    """Write ``<workspace>/.archon/config.yaml`` with the selection's config.

    Returns the written path, or None when ``selection.config_yaml`` is
    None (backend_default — no override).
    """
    if selection.config_yaml is None:
        return None
    config_dir = workspace_path / ".archon"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(selection.config_yaml, sort_keys=True), encoding="utf-8")
    return config_path
