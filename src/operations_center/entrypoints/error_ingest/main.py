# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Runtime error ingestion service (S8-8).

Ingests runtime errors from two sources and converts them to Plane tasks:

1. **HTTP webhook receiver** — accepts POST requests at ``/ingest`` with a JSON
   body.  Any service (Sentry, PagerDuty, custom alerting) can POST here.
   Expected payload::

       {
           "title": "NullPointerException in PaymentService",
           "body": "Stack trace...",         # optional
           "severity": "error",              # optional: debug/info/warning/error/critical
           "source": "sentry",              # optional: free-text label
           "repo_key": "my_service"         # optional; falls back to default_repo_key
       }

2. **Log file tail watcher** — tails configured log files line by line and
   creates tasks for lines matching a configurable pattern (default: ERROR or
   CRITICAL lines).  Deduplicates using a per-(repo_key, pattern-hash) cooldown
   window to prevent task floods from repeated error lines.

Run as standalone process::

    python -m operations_center.entrypoints.error_ingest.main \\
        --config config/operations_center.local.yaml \\
        --watch

Or one-shot (processes queued events and exits)::

    python -m operations_center.entrypoints.error_ingest.main \\
        --config config/operations_center.local.yaml
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import logging
import re
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from operations_center.adapters.plane import PlaneClient
from operations_center.config import load_settings

_logger = logging.getLogger(__name__)

# Dedup state lives here — keys are "<repo_key>:<hash>", value is last-created ISO timestamp
_DEDUP_STATE_PATH = Path("state/error_ingest_dedup.json")
_DEDUP_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Dedup helpers
# ---------------------------------------------------------------------------

def _dedup_key(repo_key: str, text: str) -> str:
    return f"{repo_key}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"


def _is_duplicate(key: str, window_seconds: int) -> bool:
    with _DEDUP_LOCK:
        if not _DEDUP_STATE_PATH.exists():
            return False
        try:
            state = json.loads(_DEDUP_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return False
        last_raw = state.get(key)
        if not last_raw:
            return False
        try:
            last = datetime.fromisoformat(last_raw)
            elapsed = (datetime.now(UTC) - last).total_seconds()
            return elapsed < window_seconds
        except Exception:
            return False


def _mark_created(key: str) -> None:
    with _DEDUP_LOCK:
        state: dict = {}
        if _DEDUP_STATE_PATH.exists():
            try:
                state = json.loads(_DEDUP_STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        state[key] = datetime.now(UTC).isoformat()
        _DEDUP_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DEDUP_STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

def _create_error_task(
    plane_client: PlaneClient,
    *,
    title: str,
    body: str,
    severity: str,
    source: str,
    repo_key: str,
) -> str | None:
    """Create a Plane task for the error event. Returns task ID or None on failure."""
    priority = "high" if severity in ("error", "critical") else "medium"
    description = (
        f"## Execution\nrepo: {repo_key}\nmode: goal\n\n"
        f"## Goal\nInvestigate and resolve the runtime error: {title}\n\n"
        f"## Context\n"
        f"- source: {source}\n"
        f"- severity: {severity}\n"
        f"- detected_at: {datetime.now(UTC).isoformat()}\n"
    )
    if body:
        description += f"\n## Error Detail\n```\n{body[:2000]}\n```\n"

    try:
        issue = plane_client.create_issue(
            name=f"[Runtime] {title[:120]}",
            description=description,
            state="Ready for AI",
            label_names=["task-kind: goal", f"priority: {priority}", f"repo: {repo_key}", "source: error-ingest"],
        )
        task_id = str(issue.get("id", ""))
        _logger.info(json.dumps({
            "event": "error_ingest_task_created",
            "task_id": task_id,
            "source": source,
            "severity": severity,
            "repo_key": repo_key,
        }, ensure_ascii=False))
        return task_id
    except Exception as exc:
        _logger.warning(json.dumps({
            "event": "error_ingest_task_failed",
            "error": str(exc),
            "title": title,
        }, ensure_ascii=False))
        return None


# ---------------------------------------------------------------------------
# Webhook receiver
# ---------------------------------------------------------------------------

def _make_webhook_handler(plane_client: PlaneClient, default_repo_key: str):
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass  # suppress default access log; structured logging handles it

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/ingest":
                self.send_response(404)
                self.end_headers()
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8", errors="replace")
                payload = json.loads(body) if body else {}
            except Exception:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "bad request"}')
                return

            title = str(payload.get("title", "Runtime error")).strip() or "Runtime error"
            detail = str(payload.get("body", ""))
            severity = str(payload.get("severity", "error")).lower()
            source = str(payload.get("source", "webhook"))
            repo_key = str(payload.get("repo_key", default_repo_key)).strip() or default_repo_key

            key = _dedup_key(repo_key, title)
            if _is_duplicate(key, window_seconds=3600):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status": "duplicate"}')
                return

            task_id = _create_error_task(
                plane_client,
                title=title,
                body=detail,
                severity=severity,
                source=source,
                repo_key=repo_key,
            )
            _mark_created(key)
            self.send_response(200)
            self.end_headers()
            resp = json.dumps({"status": "ok", "task_id": task_id}, ensure_ascii=False).encode()
            self.wfile.write(resp)

    return _Handler


def run_webhook_server(plane_client: PlaneClient, *, port: int, default_repo_key: str) -> None:
    handler = _make_webhook_handler(plane_client, default_repo_key)
    server = http.server.ThreadingHTTPServer(("", port), handler)
    _logger.info(json.dumps({"event": "error_ingest_webhook_started", "port": port}, ensure_ascii=False))
    server.serve_forever()


# ---------------------------------------------------------------------------
# Log file tail watcher
# ---------------------------------------------------------------------------

def _tail_log_file(
    plane_client: PlaneClient,
    *,
    path: str,
    repo_key: str,
    pattern: str,
    dedup_window_seconds: int,
    stop_event: threading.Event,
) -> None:
    log_path = Path(path)
    compiled = re.compile(pattern, re.IGNORECASE)
    _logger.info(json.dumps({"event": "error_ingest_tail_start", "path": path, "repo_key": repo_key}, ensure_ascii=False))

    # Seek to end of file initially to avoid replaying historical errors
    try:
        offset = log_path.stat().st_size if log_path.exists() else 0
    except Exception:
        offset = 0

    while not stop_event.is_set():
        try:
            if not log_path.exists():
                time.sleep(5)
                continue
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                while not stop_event.is_set():
                    line = f.readline()
                    if not line:
                        time.sleep(1)
                        break
                    offset = f.tell()
                    if not compiled.search(line):
                        continue
                    # Use first 200 chars of line as the title
                    title = line.strip()[:200] or "Log error"
                    key = _dedup_key(repo_key, title)
                    if _is_duplicate(key, window_seconds=dedup_window_seconds):
                        continue
                    _create_error_task(
                        plane_client,
                        title=title,
                        body=line.strip(),
                        severity="error",
                        source=f"log:{Path(path).name}",
                        repo_key=repo_key,
                    )
                    _mark_created(key)
        except Exception as exc:
            _logger.warning(json.dumps({
                "event": "error_ingest_tail_error",
                "path": path,
                "error": str(exc),
            }, ensure_ascii=False))
            time.sleep(10)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Runtime error ingestion service")
    parser.add_argument("--config", required=True)
    parser.add_argument("--watch", action="store_true", help="Run continuously (webhook + log tail)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings(args.config)

    ingest_cfg = getattr(settings, "error_ingest", None)
    if ingest_cfg is None:
        print("error_ingest not configured in settings — nothing to do.")
        return

    client = PlaneClient(
        base_url=settings.plane.base_url,
        api_token=settings.plane_token(),
        workspace_slug=settings.plane.workspace_slug,
        project_id=settings.plane.project_id,
    )

    try:
        if not args.watch:
            print("error_ingest: use --watch to run the webhook/tail loop.")
            return

        stop_event = threading.Event()
        threads: list[threading.Thread] = []

        # Start webhook server in background thread if configured
        if ingest_cfg.webhook_port > 0:
            t = threading.Thread(
                target=run_webhook_server,
                args=(client,),
                kwargs={"port": ingest_cfg.webhook_port, "default_repo_key": ingest_cfg.default_repo_key},
                daemon=True,
            )
            t.start()
            threads.append(t)

        # Start a tail thread per log source
        for src in ingest_cfg.log_sources:
            t = threading.Thread(
                target=_tail_log_file,
                args=(client,),
                kwargs={
                    "path": src.path,
                    "repo_key": src.repo_key,
                    "pattern": src.pattern,
                    "dedup_window_seconds": src.dedup_window_seconds,
                    "stop_event": stop_event,
                },
                daemon=True,
            )
            t.start()
            threads.append(t)

        if not threads:
            print("error_ingest: no webhook_port or log_sources configured — nothing to start.")
            return

        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            _logger.info(json.dumps({"event": "error_ingest_stopping"}, ensure_ascii=False))
            stop_event.set()
    finally:
        client.close()


if __name__ == "__main__":
    main()
