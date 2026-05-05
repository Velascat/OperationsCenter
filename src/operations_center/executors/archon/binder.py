# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
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

# Friendly model aliases → Archon-recognized model identifiers.
# Kept tight; expand only when needed.
_MODEL_ALIASES = {
    ("anthropic", "opus"):   "claude-opus-4",
    ("anthropic", "sonnet"): "claude-sonnet-4",
    ("anthropic", "haiku"):  "claude-haiku-4",
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


def _resolve_model(provider: str, model: str) -> str:
    """Resolve a friendly alias (e.g. opus → claude-opus-4) for known providers."""
    return _MODEL_ALIASES.get((provider, model), model)


def bind(rb: Optional[RuntimeBindingSummary]) -> ArchonConfigSelection:
    if rb is None or rb.kind == "backend_default":
        return ArchonConfigSelection(config_yaml=None, label="archon_default")

    if rb.kind == "cli_subscription":
        provider = (rb.provider or "anthropic").lower()
        if provider != "anthropic":
            raise BindError(
                f"cli_subscription only supports provider=anthropic for Archon today; "
                f"got provider={rb.provider!r}"
            )
        model = (rb.model or "sonnet").lower()
        resolved = _resolve_model(provider, model)
        cfg = {"provider": provider, "model": resolved}
        return ArchonConfigSelection(
            config_yaml=cfg, label=f"cli_subscription_{model}",
            provider=provider, model=resolved,
        )

    if rb.kind == "hosted_api":
        provider = (rb.provider or "").lower()
        if not provider:
            raise BindError("hosted_api binding requires provider")
        if not rb.model:
            raise BindError("hosted_api binding requires model")
        model = _resolve_model(provider, rb.model.lower())
        cfg = {"provider": provider, "model": model}
        if rb.endpoint:
            cfg["base_url"] = rb.endpoint
        return ArchonConfigSelection(
            config_yaml=cfg, label=f"hosted_{provider}_{model}",
            provider=provider, model=model,
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
