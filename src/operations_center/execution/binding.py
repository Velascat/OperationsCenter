# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""bind_execution_target — convert CxRP envelope → OC bound target.

This is the explicit narrowing step. Unknown backends/executors from
the wire die here, not at the adapter layer. Provenance is resolved
from OC's upstream registry, not from CxRP.

Errors are typed (UnknownBackendError, UnknownExecutorError, etc.) so
callers can distinguish recoverable mismatches from policy violations.
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
    """Base class for all binding errors."""


class UnknownBackendError(TargetBindError):
    """CxRP envelope named a backend OC doesn't recognize."""


class UnknownExecutorError(TargetBindError):
    """CxRP envelope named an executor OC doesn't recognize."""


class InvalidRuntimeBindingError(TargetBindError):
    """RuntimeBinding violates the validity table or optional-field allow-list."""


class PolicyViolationError(TargetBindError):
    """The bound target would violate execution policy."""


class MissingProvenanceError(TargetBindError):
    """The catalog requires a backend's provenance but none was resolved."""


# ── Catalog + policy protocols (avoid hard imports — keep test surface lean) ──


class CatalogLike(Protocol):
    """The subset of ExecutorCatalog ``bind`` needs.

    A real ExecutorCatalog from ``operations_center.executors.catalog``
    satisfies this Protocol implicitly.
    """

    @property
    def entries(self) -> dict: ...


class PolicyLike(Protocol):
    """A no-op default policy is fine; production OC plugs its real
    ExecutionPolicy in here."""

    def allows(self, target: BoundExecutionTarget) -> tuple[bool, str]: ...


class _AlwaysAllowPolicy:
    def allows(self, target: BoundExecutionTarget) -> tuple[bool, str]:
        return True, ""


# ── Provenance resolution ────────────────────────────────────────────────


def _provenance_from_registry(backend_id: str) -> Optional[BackendProvenance]:
    """Look up the backend in OC's upstream fork registry.

    Returns None when the backend isn't in the registry (e.g. direct_local
    is not forked). Returns a fully-populated BackendProvenance otherwise.
    """
    try:
        from operations_center.upstream.registry import load_registry
        from operations_center.upstream.patches import load_patches
    except ImportError:
        return None

    registry = load_registry()
    if backend_id not in registry.entries:
        return None
    entry = registry.entries[backend_id]
    patches = load_patches().for_fork(backend_id)
    return BackendProvenance(
        source="registry",
        repo=entry.fork.repo,
        ref=entry.fork_commit,
        patches=[p.id for p in patches],
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
            model=rb.model,
            provider=rb.provider,
            endpoint=rb.endpoint,
            config_ref=rb.config_ref,
        )
    except (AttributeError, TypeError) as exc:
        raise InvalidRuntimeBindingError(
            f"runtime_binding object missing required field: {exc}"
        ) from exc


def _narrow_lane(value: str | None) -> str:
    """Lane is the CxRP abstract category — kept as string in OC.

    OC's strict enum is ``LaneName`` (executor-shaped), which is a
    different concept; that goes through ``_narrow_executor``.
    """
    if value is None:
        raise UnknownExecutorError("envelope.lane is required")
    return str(value)


def _narrow_backend(value: str | None) -> BackendName:
    if value is None:
        raise UnknownBackendError("envelope.backend is required for binding")
    try:
        return BackendName(value)
    except ValueError as exc:
        valid = sorted(b.value for b in BackendName)
        raise UnknownBackendError(
            f"unknown backend {value!r}; OC recognizes: {valid}"
        ) from exc


def _narrow_executor(value: str | None) -> Optional[LaneName]:
    if value is None:
        return None
    try:
        return LaneName(value)
    except ValueError as exc:
        valid = sorted(l.value for l in LaneName)
        raise UnknownExecutorError(
            f"unknown executor {value!r}; OC recognizes: {valid}"
        ) from exc


# ── Public binding API ──────────────────────────────────────────────────


def bind_execution_target(
    envelope: ExecutionTargetEnvelope,
    *,
    catalog: Optional[CatalogLike] = None,
    policy: Optional[PolicyLike] = None,
    require_provenance: bool = False,
) -> BoundExecutionTarget:
    """Validate a CxRP envelope and produce a BoundExecutionTarget.

    Raises typed errors (UnknownBackendError / UnknownExecutorError /
    InvalidRuntimeBindingError / PolicyViolationError /
    MissingProvenanceError) for structured handling.

    Args:
        envelope: The wire-shape execution target intent.
        catalog: Optional ExecutorCatalog. If supplied, the bound
            backend must be present (else UnknownBackendError).
        policy: Optional ExecutionPolicy. If supplied, the bound target
            is validated against it.
        require_provenance: When True, MissingProvenanceError is raised
            if the registered backend has no upstream registry entry.
            Production callers (Phase 14 catalog-strict mode) set True;
            tests / dev typically leave False.
    """
    lane = _narrow_lane(envelope.lane.value if hasattr(envelope.lane, "value") else envelope.lane)
    backend = _narrow_backend(envelope.backend)
    executor = _narrow_executor(envelope.executor)

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
        lane=lane,
        backend=backend,
        executor=executor,
        runtime_binding=runtime_summary,
        provenance=provenance,
    )

    pol = policy or _AlwaysAllowPolicy()
    ok, reason = pol.allows(target)
    if not ok:
        raise PolicyViolationError(f"policy rejected bound target: {reason}")

    return target
