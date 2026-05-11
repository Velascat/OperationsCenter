# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""runtime_binding_policy — task-shape-driven RuntimeBinding selection.

Closes the request-time model-selection gap. SwitchBoard picks the lane
(claude_cli vs codex_cli vs aider_local); OC's policy layer is responsible
for picking what powers it (opus vs sonnet vs haiku, etc.). This module is
the picker.

The policy is rule-based and YAML-driven. Each rule declares a
``when:`` filter over (task_type, lane) and a ``bind:`` block describing
the resulting RuntimeBinding. The first matching rule wins; the optional
``default:`` block applies when no rule matches.

Rules produce a ``cxrp.contracts.RuntimeBinding`` (the canonical type),
which CxRP validates on construction against its kind × selection_mode
validity table. The coordinator then carries that canonical type through
OC's compatibility import surface on ``ExecutionRequest.runtime_binding``.

The default policy bundled with OC reflects the team's current cost/quality
defaults: opus for refactor/feature, sonnet for tests, haiku for lint
fixes, sonnet as the catch-all. See ``config/runtime_binding_policy.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from cxrp.contracts.runtime_binding import RuntimeBinding
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.contracts import OcPlanningProposal, OcRoutingDecision

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeBindingRule:
    """One rule in the runtime-binding policy.

    ``when`` matches against ``(task_type, lane)`` extracted from the
    proposal/decision pair. Empty ``when`` means "match anything"
    (typically used inside the default block).
    """

    name: str
    when: dict[str, str]  # e.g. {"task_type": "refactor", "lane": "claude_cli"}
    kind: str
    model: str | None = None
    provider: str | None = None
    endpoint: str | None = None
    config_ref: str | None = None

    def matches(self, attrs: dict[str, str]) -> bool:
        for k, v in self.when.items():
            if attrs.get(k) != v:
                return False
        return True

    def to_binding(self) -> RuntimeBinding:
        """Build the canonical CxRP RuntimeBinding. Raises ValueError on invalid combos."""
        return RuntimeBinding(
            kind=RuntimeKind(self.kind),
            selection_mode=SelectionMode.POLICY_SELECTED,
            model=self.model,
            provider=self.provider,
            endpoint=self.endpoint,
            config_ref=self.config_ref,
        )


@dataclass(frozen=True)
class RuntimeBindingPolicy:
    """Ordered list of rules + an optional default."""

    rules: tuple[RuntimeBindingRule, ...]
    default: RuntimeBindingRule | None = None

    def select(
        self,
        proposal: OcPlanningProposal,
        decision: OcRoutingDecision,
    ) -> RuntimeBinding | None:
        """Return the first-matching rule's binding, or the default's, or None.

        ``None`` means "no binding selected" — kodo (and other adapters)
        will fall back to their built-in defaults. This is the pre-policy
        behaviour and is preserved when no rule and no default match.
        """
        attrs = {
            "task_type": proposal.task_type.value,
            "lane": decision.selected_lane.value,
        }
        for rule in self.rules:
            if rule.matches(attrs):
                logger.debug(
                    "RuntimeBindingPolicy: rule=%s matched task_type=%s lane=%s",
                    rule.name, attrs["task_type"], attrs["lane"],
                )
                return rule.to_binding()
        if self.default is not None:
            logger.debug(
                "RuntimeBindingPolicy: no rule matched, applying default=%s",
                self.default.name,
            )
            return self.default.to_binding()
        return None

    @classmethod
    def from_yaml(cls, path: Path | str) -> RuntimeBindingPolicy:
        """Load a policy from a YAML file. Returns an empty policy if missing."""
        p = Path(path)
        if not p.exists():
            logger.info("RuntimeBindingPolicy: %s not found, returning empty policy", p)
            return cls(rules=())
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> RuntimeBindingPolicy:
        rules: list[RuntimeBindingRule] = []
        for entry in raw.get("rules") or []:
            rules.append(_rule_from_dict(entry))
        default_block = raw.get("default")
        default = _rule_from_dict({"name": "default", "when": {}, **default_block}) if default_block else None
        return cls(rules=tuple(rules), default=default)


def _rule_from_dict(entry: dict[str, Any]) -> RuntimeBindingRule:
    bind = entry.get("bind") or {}
    return RuntimeBindingRule(
        name=entry.get("name", "rule"),
        when=dict(entry.get("when") or {}),
        kind=bind.get("kind", "backend_default"),
        model=bind.get("model"),
        provider=bind.get("provider"),
        endpoint=bind.get("endpoint"),
        config_ref=bind.get("config_ref"),
    )


# ---------------------------------------------------------------------------
# Default policy — bundled fallback when no config file is provided
# ---------------------------------------------------------------------------

# Sensible defaults: opus for heavy work, sonnet for medium, haiku for cheap.
# Operators override by writing config/runtime_binding_policy.yaml.
DEFAULT_POLICY = RuntimeBindingPolicy(
    rules=(
        RuntimeBindingRule(
            name="refactor_premium",
            when={"task_type": "refactor", "lane": "claude_cli"},
            kind="cli_subscription",
            provider="anthropic",
            model="opus",
        ),
        RuntimeBindingRule(
            name="feature_premium",
            when={"task_type": "feature", "lane": "claude_cli"},
            kind="cli_subscription",
            provider="anthropic",
            model="opus",
        ),
        RuntimeBindingRule(
            name="test_balanced",
            when={"task_type": "test_fix", "lane": "claude_cli"},
            kind="cli_subscription",
            provider="anthropic",
            model="sonnet",
        ),
        RuntimeBindingRule(
            name="lint_cheap",
            when={"task_type": "lint_fix", "lane": "claude_cli"},
            kind="cli_subscription",
            provider="anthropic",
            model="haiku",
        ),
    ),
    default=RuntimeBindingRule(
        name="default_balanced",
        when={},
        kind="cli_subscription",
        provider="anthropic",
        model="sonnet",
    ),
)
