# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""HTTP client helpers for talking to a running Archon instance.

Connectivity-level helpers: health probe, conversation create, run
lookup, run cancel/abandon, workflow listing. Higher-level orchestration
(kickoff → poll-until-terminal → status mapping) lives in
``http_workflow.py``.

Archon is deployed by PlatformDeployment
(``compose/profiles/archon.yml`` in the PlatformDeployment repo) at
``http://localhost:3000`` by default.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx


DEFAULT_BASE_URL = "http://localhost:3000"
HEALTH_PATH = "/api/health"


@dataclass(frozen=True)
class HealthProbeResult:
    """Outcome of a single health probe against an Archon instance."""

    ok: bool
    base_url: str
    status_code: int | None
    summary: str

    @property
    def reachable(self) -> bool:
        return self.status_code is not None


def archon_health_probe(
    base_url: str = DEFAULT_BASE_URL,
    *,
    timeout_seconds: float = 5.0,
    client: httpx.Client | None = None,
) -> HealthProbeResult:
    """Probe Archon's ``/api/health`` endpoint.

    Returns a structured result rather than raising — callers
    (the concrete adapter, ops scripts, monitors) decide what to do.
    """
    url = base_url.rstrip("/") + HEALTH_PATH
    owns_client = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.get(url)
        except httpx.TimeoutException:
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary=f"timeout after {timeout_seconds}s probing {url}",
            )
        except httpx.ConnectError as exc:
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary=f"connect error: {exc}",
            )
        except Exception as exc:  # any other transport-level failure
            return HealthProbeResult(
                ok=False, base_url=base_url, status_code=None,
                summary=f"http error: {exc}",
            )
    finally:
        if owns_client:
            client.close()

    ok = 200 <= response.status_code < 300
    summary = (
        "archon healthy" if ok
        else f"archon unhealthy (HTTP {response.status_code})"
    )
    return HealthProbeResult(
        ok=ok, base_url=base_url, status_code=response.status_code,
        summary=summary,
    )


# ──────────────────────────────────────────────────────────────────────────
# Conversation + workflow-run helpers (added for real-workflow integration)
# See docs/architecture/adapters/archon-real-workflow-integration.md
# ──────────────────────────────────────────────────────────────────────────

CONVERSATIONS_PATH = "/api/conversations"
WORKFLOW_RUN_PATH = "/api/workflows/runs"
BY_WORKER_PATH = "/api/workflows/runs/by-worker"
WORKFLOWS_LIST_PATH = "/api/workflows"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConversationCreateResult:
    """Result of POST /api/conversations.

    ``ok`` is True only when Archon returned 200 with a valid body.
    Failures (network, non-200, malformed JSON) populate ``error_summary``
    and leave ``conversation_id`` empty.
    """

    ok: bool
    conversation_id: str
    db_id: str
    dispatched: bool
    error_summary: str | None = None


@dataclass(frozen=True)
class WorkflowRunDetail:
    """Result of GET /api/workflows/runs/{runId} or .../by-worker/{cid}.

    ``run_id`` is empty when the run hasn't been registered yet (by-worker
    returns 404 in that case — caller polls). ``events`` is the full list
    from the API, untouched, for downstream observability retention.
    """

    ok: bool
    run_id: str
    status: str
    metadata: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    error_summary: str | None = None


@dataclass(frozen=True)
class WorkflowSummary:
    """One entry from GET /api/workflows."""

    name: str
    description: str


def archon_create_conversation(
    base_url: str = DEFAULT_BASE_URL,
    *,
    codebase_id: str | None = None,
    timeout_seconds: float = 5.0,
    client: "httpx.Client | None" = None,
) -> ConversationCreateResult:
    """POST /api/conversations to create a fresh conversation.

    Per the design doc (D2), each ExecutionRequest gets its own
    conversation. ``codebase_id`` is optional in v1 — Archon infers when
    omitted.
    """
    url = base_url.rstrip("/") + CONVERSATIONS_PATH
    body: dict[str, Any] = {}
    if codebase_id:
        body["codebaseId"] = codebase_id

    owns = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.post(url, json=body)
        except Exception as exc:
            return ConversationCreateResult(
                ok=False, conversation_id="", db_id="", dispatched=False,
                error_summary=f"http error creating conversation: {exc}",
            )
    finally:
        if owns:
            client.close()

    if response.status_code != 200:
        preview = response.text[:200] if response.text else ""
        return ConversationCreateResult(
            ok=False, conversation_id="", db_id="", dispatched=False,
            error_summary=f"HTTP {response.status_code}: {preview}".strip(),
        )

    try:
        payload = response.json()
    except Exception as exc:
        return ConversationCreateResult(
            ok=False, conversation_id="", db_id="", dispatched=False,
            error_summary=f"non-JSON response: {exc}",
        )

    return ConversationCreateResult(
        ok=True,
        conversation_id=str(payload.get("conversationId", "")),
        db_id=str(payload.get("id", "")),
        dispatched=bool(payload.get("dispatched", False)),
    )


def archon_get_run_by_worker(
    base_url: str,
    conversation_id: str,
    *,
    timeout_seconds: float = 5.0,
    client: "httpx.Client | None" = None,
) -> WorkflowRunDetail:
    """GET /api/workflows/runs/by-worker/{platformId}.

    Response shape is ``{run: WorkflowRun}`` only — no events. Use
    ``archon_get_run_detail(run_id)`` to fetch the full ``{run, events}``
    payload once the run_id is known.

    Returns ``ok=False`` with HTTP 404 surfaced for "run not registered yet"
    so callers can distinguish "still pending" from real errors.
    """
    url = base_url.rstrip("/") + f"{BY_WORKER_PATH}/{conversation_id}"
    owns = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.get(url)
        except Exception as exc:
            return WorkflowRunDetail(
                ok=False, run_id="", status="",
                metadata={}, error_summary=f"http error: {exc}",
            )
    finally:
        if owns:
            client.close()

    if response.status_code == 404:
        return WorkflowRunDetail(
            ok=False, run_id="", status="",
            metadata={}, error_summary="not registered yet (404)",
        )
    if response.status_code != 200:
        preview = response.text[:200] if response.text else ""
        return WorkflowRunDetail(
            ok=False, run_id="", status="",
            metadata={}, error_summary=f"HTTP {response.status_code}: {preview}".strip(),
        )
    try:
        payload = response.json()
    except Exception as exc:
        return WorkflowRunDetail(
            ok=False, run_id="", status="",
            metadata={}, error_summary=f"non-JSON: {exc}",
        )

    run = payload.get("run") or {}
    return WorkflowRunDetail(
        ok=True,
        run_id=str(run.get("id", "")),
        status=str(run.get("status", "")),
        metadata=dict(run.get("metadata") or {}),
        events=[],  # by-worker response has no events; use archon_get_run_detail.
    )


def archon_get_run_detail(
    base_url: str,
    run_id: str,
    *,
    timeout_seconds: float = 5.0,
    client: "httpx.Client | None" = None,
) -> WorkflowRunDetail:
    """GET /api/workflows/runs/{runId} — full detail with events.

    Used after the dispatcher's polling phase concludes, to retrieve the
    complete event list for downstream observability/normalization.
    """
    if not run_id:
        return WorkflowRunDetail(
            ok=False, run_id="", status="",
            metadata={}, error_summary="run_id is empty",
        )
    url = base_url.rstrip("/") + f"{WORKFLOW_RUN_PATH}/{run_id}"
    owns = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.get(url)
        except Exception as exc:
            return WorkflowRunDetail(
                ok=False, run_id=run_id, status="",
                metadata={}, error_summary=f"http error: {exc}",
            )
    finally:
        if owns:
            client.close()

    if response.status_code != 200:
        preview = response.text[:200] if response.text else ""
        return WorkflowRunDetail(
            ok=False, run_id=run_id, status="",
            metadata={},
            error_summary=f"HTTP {response.status_code}: {preview}".strip(),
        )
    try:
        payload = response.json()
    except Exception as exc:
        return WorkflowRunDetail(
            ok=False, run_id=run_id, status="",
            metadata={}, error_summary=f"non-JSON: {exc}",
        )

    run = payload.get("run") or {}
    return WorkflowRunDetail(
        ok=True,
        run_id=str(run.get("id", run_id)),
        status=str(run.get("status", "")),
        metadata=dict(run.get("metadata") or {}),
        events=list(payload.get("events") or []),
    )


def archon_abandon_run(
    base_url: str,
    run_id: str,
    *,
    timeout_seconds: float = 5.0,
    client: "httpx.Client | None" = None,
) -> bool:
    """POST /api/workflows/runs/{runId}/abandon. Best-effort; failures logged."""
    if not run_id:
        return False
    url = base_url.rstrip("/") + f"{WORKFLOW_RUN_PATH}/{run_id}/abandon"
    owns = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.post(url, json={})
        except Exception as exc:
            logger.warning("archon_abandon_run failed for %s: %s", run_id, exc)
            return False
    finally:
        if owns:
            client.close()
    if response.status_code >= 300:
        logger.warning(
            "archon_abandon_run %s returned HTTP %s", run_id, response.status_code,
        )
        return False
    return True


def archon_cancel_run(
    base_url: str,
    run_id: str,
    *,
    timeout_seconds: float = 5.0,
    client: "httpx.Client | None" = None,
) -> bool:
    """POST /api/workflows/runs/{runId}/cancel. Best-effort; failures logged."""
    if not run_id:
        return False
    url = base_url.rstrip("/") + f"{WORKFLOW_RUN_PATH}/{run_id}/cancel"
    owns = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.post(url, json={})
        except Exception as exc:
            logger.warning("archon_cancel_run failed for %s: %s", run_id, exc)
            return False
    finally:
        if owns:
            client.close()
    if response.status_code >= 300:
        logger.warning(
            "archon_cancel_run %s returned HTTP %s", run_id, response.status_code,
        )
        return False
    return True


def archon_list_workflows(
    base_url: str = DEFAULT_BASE_URL,
    *,
    timeout_seconds: float = 5.0,
    client: "httpx.Client | None" = None,
) -> list[WorkflowSummary]:
    """GET /api/workflows — list registered workflow names.

    Returns an empty list on any failure (network/non-200/malformed). Used by
    the operations-center-archon-probe --list-workflows option for operator
    verification before running.
    """
    url = base_url.rstrip("/") + WORKFLOWS_LIST_PATH
    owns = client is None
    client = client or httpx.Client(timeout=timeout_seconds)
    try:
        try:
            response = client.get(url)
        except Exception as exc:
            logger.warning("archon_list_workflows failed: %s", exc)
            return []
    finally:
        if owns:
            client.close()
    if response.status_code != 200:
        return []
    try:
        payload = response.json()
    except Exception:
        return []

    items = payload if isinstance(payload, list) else payload.get("workflows") or []
    out: list[WorkflowSummary] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(WorkflowSummary(
            name=str(item.get("name", "")),
            description=str(item.get("description", "")),
        ))
    return out
