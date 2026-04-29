# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""S10-8: Real-time CI webhook receiver.

Listens for GitHub ``check_run`` webhook events and triggers an immediate
autonomy-cycle run when a PR's CI checks complete (either success or failure).
This eliminates the polling delay between CI completing and OperationsCenter acting.

Security: all incoming requests are validated against the ``X-Hub-Signature-256``
HMAC header using the secret from ``OPERATIONS_CENTER_WEBHOOK_SECRET``.  Requests
without a valid signature are rejected with HTTP 401.

Environment variables:
  OPERATIONS_CENTER_WEBHOOK_SECRET   — HMAC secret set in GitHub webhook settings
  OPERATIONS_CENTER_WEBHOOK_PORT     — Port to listen on (default: 8765)
  OPERATIONS_CENTER_WEBHOOK_HOST     — Host to bind to (default: 127.0.0.1)
  OPERATIONS_CENTER_WEBHOOK_TRIGGER  — Optional command to run on a CI event
                                   (default: write a trigger file)

Usage::

    python -m operations_center.entrypoints.ci_webhook.main
    # or via the CI monitor entrypoint which manages both polling + webhook
"""
from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

_SECRET_ENV = "OPERATIONS_CENTER_WEBHOOK_SECRET"
_PORT_ENV = "OPERATIONS_CENTER_WEBHOOK_PORT"
_HOST_ENV = "OPERATIONS_CENTER_WEBHOOK_HOST"
_TRIGGER_ENV = "OPERATIONS_CENTER_WEBHOOK_TRIGGER"

_DEFAULT_PORT = 8765
_DEFAULT_HOST = "127.0.0.1"
_TRIGGER_DIR = Path("state/ci_webhook_triggers")

# Events we care about
_RELEVANT_ACTIONS = {"completed"}
_RELEVANT_CONCLUSIONS = {"success", "failure", "action_required", "cancelled", "timed_out"}


def _get_secret() -> bytes | None:
    """Return the HMAC secret bytes or None when not configured."""
    secret = os.environ.get(_SECRET_ENV, "").strip()
    return secret.encode() if secret else None


def _verify_signature(body: bytes, signature_header: str, secret: bytes) -> bool:
    """Return True when the ``X-Hub-Signature-256`` header matches the HMAC of *body*."""
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _parse_check_run_event(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the fields we care about from a ``check_run`` payload.

    Returns a dict with ``repo``, ``pr_number``, ``conclusion``, ``check_name``,
    and ``head_sha`` keys, or None when the event is irrelevant.
    """
    action = str(payload.get("action") or "")
    if action not in _RELEVANT_ACTIONS:
        return None

    check_run = payload.get("check_run") or {}
    conclusion = str(check_run.get("conclusion") or "")
    if conclusion not in _RELEVANT_CONCLUSIONS:
        return None

    check_name = str(check_run.get("name") or "")
    head_sha = str(check_run.get("head_sha") or "")

    # Extract PR number from pull_requests if present
    pull_requests = check_run.get("pull_requests") or []
    pr_number: int | None = None
    if pull_requests:
        pr_number = int((pull_requests[0].get("number") or 0))

    repo = payload.get("repository") or {}
    repo_full_name = str(repo.get("full_name") or "")

    return {
        "repo": repo_full_name,
        "pr_number": pr_number,
        "conclusion": conclusion,
        "check_name": check_name,
        "head_sha": head_sha,
        "received_at": datetime.now(UTC).isoformat(),
    }


def _write_trigger(event: dict[str, Any]) -> None:
    """Write a trigger file so the autonomy cycle or reviewer watcher can pick it up."""
    _TRIGGER_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"ci_{event['head_sha'][:12]}_{event['conclusion']}.json"
    (_TRIGGER_DIR / fname).write_text(json.dumps(event, indent=2))
    _logger.info(json.dumps({
        "event": "ci_webhook_trigger_written",
        "repo": event.get("repo"),
        "pr_number": event.get("pr_number"),
        "conclusion": event.get("conclusion"),
        "check_name": event.get("check_name"),
        "trigger_file": fname,
    }))


def _run_trigger_command(event: dict[str, Any]) -> None:
    """Run the configured trigger command when a CI event arrives."""
    cmd = os.environ.get(_TRIGGER_ENV, "").strip()
    if not cmd:
        _write_trigger(event)
        return
    import subprocess
    env = dict(os.environ)
    env["CP_CI_REPO"] = event.get("repo", "")
    env["CP_CI_CONCLUSION"] = event.get("conclusion", "")
    env["CP_CI_PR_NUMBER"] = str(event.get("pr_number") or "")
    env["CP_CI_HEAD_SHA"] = event.get("head_sha", "")
    env["CP_CI_CHECK_NAME"] = event.get("check_name", "")
    try:
        subprocess.Popen(cmd.split(), env=env)
        _logger.info(json.dumps({
            "event": "ci_webhook_trigger_command",
            "cmd": cmd,
            "conclusion": event.get("conclusion"),
        }))
    except Exception as exc:
        _logger.warning(json.dumps({
            "event": "ci_webhook_trigger_command_failed",
            "error": str(exc)[:200],
        }))
        _write_trigger(event)  # Fall back to file trigger


class _WebhookHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for GitHub webhook events."""

    # Shared secret — set by the server thread before starting
    webhook_secret: bytes | None = None

    def do_POST(self) -> None:
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        # HMAC validation
        secret = self.__class__.webhook_secret
        if secret:
            sig = self.headers.get("X-Hub-Signature-256", "")
            if not _verify_signature(body, sig, secret):
                _logger.warning(json.dumps({"event": "ci_webhook_invalid_signature"}))
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Invalid signature")
                return

        event_type = self.headers.get("X-GitHub-Event", "")
        if event_type != "check_run":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Ignored")
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid JSON")
            return

        event = _parse_check_run_event(payload)
        if event:
            # Handle in a background thread so we don't block GitHub's delivery
            threading.Thread(target=_run_trigger_command, args=(event,), daemon=True).start()

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Redirect HTTP access log to the Python logger."""
        _logger.debug("ci_webhook: " + format, *args)


def serve(*, host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT, secret: bytes | None = None) -> None:
    """Start the webhook HTTP server (blocking)."""
    _WebhookHandler.webhook_secret = secret
    server = http.server.HTTPServer((host, port), _WebhookHandler)
    _logger.info(json.dumps({
        "event": "ci_webhook_server_start",
        "host": host,
        "port": port,
        "hmac_enabled": secret is not None,
    }))
    server.serve_forever()


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Receive GitHub check_run webhooks and trigger OperationsCenter cycles."
    )
    parser.add_argument("--host", default=os.environ.get(_HOST_ENV, _DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get(_PORT_ENV, _DEFAULT_PORT)))
    args = parser.parse_args()

    secret = _get_secret()
    if not secret:
        _logger.warning(json.dumps({
            "event": "ci_webhook_no_secret",
            "advice": f"Set {_SECRET_ENV} to enable HMAC signature validation.",
        }))

    serve(host=args.host, port=args.port, secret=secret)


if __name__ == "__main__":
    main()
