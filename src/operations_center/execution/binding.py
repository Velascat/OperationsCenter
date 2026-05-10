# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""bind_execution_target — convert CxRP envelope → OC bound target.

Schema 0.3 (closed-system simplification): envelope.backend and
envelope.executor are already typed CxRP enums. This binder converts
them to OC's same-valued enums and resolves provenance from the
SourceRegistry.

Errors:
  - UnknownBackendError         : envelope.backend missing or not in catalog
  - InvalidRuntimeBindingError  : RuntimeBinding object malformed
  - PolicyViolationError        : bound target rejected by policy
  - MissingProvenanceError      : registry-strict mode and no entry
"""
from __future__ import annotations

from typing import Optional, Protocol

from cxrp.contracts.execution_target import ExecutionTargetEnvelope
from cxrp.vocabulary.runtime import RuntimeKind, SelectionMode

from operations_center.contracts.enums import BackendName, LaneName
from operations_center.contracts.execution import RuntimeBindingSummary
from operations_center.execution.target import (
    BackendProvenance,
    BoundExecutionTarget,
)


# ── Typed errors ────────────────────────────────────────────────────────


class TargetBindError(ValueError):
    """Base class for binding errors."""


class UnknownBackendError(TargetBindError):
    """envelope.backend is missing, or not present in the configured
    executor catalog. Schema 0.3 already rejects unknown *names* at
    parse time; this fires for missing values or catalog mismatches.
    """


class InvalidRuntimeBindingError(TargetBindError):
    """RuntimeBinding object is malformed (missing required attrs)."""


class PolicyViolationError(TargetBindError):
    """Policy rejected the bound target."""


class MissingProvenanceError(TargetBindError):
    """``require_provenance=True`` and registry has no entry for the backend."""


class CatalogLike(Protocol):
    @property
    def entries(self) -> dict: ...


class PolicyLike(Protocol):
    def allows(self, target: BoundExecutionTarget) -> tuple[bool, str]: ...


class _AlwaysAllowPolicy:
    def allows(self, target: BoundExecutionTarget) -> tuple[bool, str]:
        return True, ""


# ── Provenance resolution (OC-only — never on the wire) ─────────────────


_DEFAULT_REGISTRY_PATH = "registry/source_registry.yaml"
_DEFAULT_PATCHES_ROOT = "registry/patches"


def _provenance_from_registry(backend_id: str) -> Optional[BackendProvenance]:
    """Resolve provenance for ``backend_id`` from the OC source registry.

    Reads ``registry/source_registry.yaml`` (relative to CWD) via the
    SourceRegistry library. Returns None when the entry doesn't exist
    or the registry can't be loaded — callers fall back to ``unknown``.
    """
    try:
        from pathlib import Path
        from source_registry import SourceRegistry, load_patches
    except ImportError:
        return None

    registry_path = Path(_DEFAULT_REGISTRY_PATH)
    if not registry_path.exists():
        return None
    try:
        registry = SourceRegistry.from_yaml(registry_path)
        entry = registry.resolve(backend_id)
    except Exception:
        return None

    repo = entry.fork_url or entry.upstream_url
    # Strip protocol/host to match old "owner/repo" provenance form
    if "github.com/" in repo:
        repo = repo.split("github.com/", 1)[1].rstrip("/")
        if repo.endswith(".git"):
            repo = repo[:-4]

    patches_root = Path(_DEFAULT_PATCHES_ROOT)
    patch_ids: list[str] = []
    if patches_root.exists():
        try:
            patch_reg = load_patches(patches_root)
            patch_ids = [p.patch_id for p in patch_reg.for_source(backend_id)]
        except Exception:
            patch_ids = []

    return BackendProvenance(
        source="registry", repo=repo, ref=entry.expected_sha, patches=patch_ids,
    )


# ── Helpers ─────────────────────────────────────────────────────────────


def _runtime_binding_to_summary(rb) -> Optional[RuntimeBindingSummary]:
    if rb is None:
        return None
    try:
        return RuntimeBindingSummary(
            kind=rb.kind.value if isinstance(rb.kind, RuntimeKind) else str(rb.kind),
            selection_mode=(
                rb.selection_mode.value
                if isinstance(rb.selection_mode, SelectionMode)
                else str(rb.selection_mode)
            ),
            model=rb.model, provider=rb.provider,
            endpoint=rb.endpoint, config_ref=rb.config_ref,
        )
    except (AttributeError, TypeError) as exc:
        raise InvalidRuntimeBindingError(
            f"runtime_binding object missing required field: {exc}"
        ) from exc


# ── Public binding API ──────────────────────────────────────────────────


def bind_execution_target(
    envelope: ExecutionTargetEnvelope,
    *,
    catalog: Optional[CatalogLike] = None,
    policy: Optional[PolicyLike] = None,
    require_provenance: bool = False,
) -> BoundExecutionTarget:
    """Convert a wire-shape envelope to a dispatch-ready BoundExecutionTarget.

    Schema 0.3: envelope.backend and envelope.executor are already typed
    CxRP enums (BackendName, ExecutorName). This binder converts them to
    OC's same-valued enums (BackendName, LaneName) — infallible, since
    the wire already validated.

    What this still does:
      - Resolves provenance from the upstream registry
      - Optionally checks catalog membership
      - Optionally runs policy
      - Validates RuntimeBinding shape (already CxRP-validated, but the
        adapter mirror needs the field-pluck)
    """
    if envelope.backend is None:
        raise UnknownBackendError("envelope.backend is required for binding")

    # Both enums share the same string values; conversion is by-value.
    backend = BackendName(envelope.backend.value)
    executor = LaneName(envelope.executor.value) if envelope.executor is not None else None
    lane = envelope.lane.value if hasattr(envelope.lane, "value") else str(envelope.lane)

    if catalog is not None and backend.value not in catalog.entries:
        raise UnknownBackendError(
            f"backend {backend.value!r} not present in executor catalog"
        )

    runtime_summary = _runtime_binding_to_summary(envelope.runtime_binding)
    provenance = _provenance_from_registry(backend.value)
    if require_provenance and provenance is None:
        raise MissingProvenanceError(
            f"backend {backend.value!r} has no provenance entry in the upstream registry"
        )

    target = BoundExecutionTarget(
        lane=lane, backend=backend, executor=executor,
        runtime_binding=runtime_summary, provenance=provenance,
    )

    pol = policy or _AlwaysAllowPolicy()
    ok, reason = pol.allows(target)
    if not ok:
        raise PolicyViolationError(f"policy rejected bound target: {reason}")

    return target
