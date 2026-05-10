# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Cross-process concurrency proof for the persistent lock store (Phase 6, Slice E).

Spawns two real Python subprocesses competing for the same repo lock and
asserts that exactly one acquires it. This is the integration test for
``fcntl.flock``-based mutual exclusion across OS processes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path


_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import json
    import os
    import socket
    import sys
    import time
    from pathlib import Path

    state_dir = Path(sys.argv[1])
    repo_id = sys.argv[2]
    hold_seconds = float(sys.argv[3])
    out_path = Path(sys.argv[4])

    sys.path.insert(0, sys.argv[5])  # OC src/

    from operations_center.audit_dispatch.errors import RepoLockAlreadyHeldError
    from operations_center.audit_dispatch.lock_store import (
        PersistentLockPayload,
        PersistentLockStore,
    )

    store = PersistentLockStore(state_dir)
    payload = PersistentLockPayload(
        repo_id=repo_id,
        run_id=f"run_{os.getpid()}",
        audit_type="audit_type_1",
        oc_pid=os.getpid(),
        started_at="2026-05-04T00:00:00Z",
        command="x",
        expected_run_status_path="/tmp/x",
    )
    result = {"pid": os.getpid(), "acquired": False, "error": None}
    try:
        store.try_acquire(payload)
        result["acquired"] = True
        time.sleep(hold_seconds)
        store.release(repo_id)
    except RepoLockAlreadyHeldError as exc:
        result["error"] = str(exc)
    out_path.write_text(json.dumps(result))
    """
)


def _run_competitor(
    state_dir: Path,
    repo_id: str,
    hold_seconds: float,
    out_path: Path,
    oc_src: Path,
) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            _SUBPROCESS_SCRIPT,
            str(state_dir),
            repo_id,
            str(hold_seconds),
            str(out_path),
            str(oc_src),
        ],
    )


def _oc_src_dir() -> Path:
    # tests/unit/audit_dispatch/test_lock_store_concurrency.py
    # parents[0]=audit_dispatch [1]=unit [2]=tests [3]=OC root
    return Path(__file__).resolve().parents[3] / "src"


class TestCrossProcessConcurrency:
    def test_only_one_subprocess_acquires(self, tmp_path: Path) -> None:
        out_a = tmp_path / "a.json"
        out_b = tmp_path / "b.json"
        oc_src = _oc_src_dir()

        # First holds the lock for 1.5s; second tries shortly after.
        proc_a = _run_competitor(tmp_path, "example_managed_repo", 1.5, out_a, oc_src)
        # Brief spin to ensure A wins the race.
        import time as _t
        _t.sleep(0.3)
        proc_b = _run_competitor(tmp_path, "example_managed_repo", 0.1, out_b, oc_src)

        proc_a.wait(timeout=10)
        proc_b.wait(timeout=10)

        result_a = json.loads(out_a.read_text())
        result_b = json.loads(out_b.read_text())

        # Exactly one acquires.
        assert result_a["acquired"] != result_b["acquired"]
        # The one that didn't acquire reports a meaningful error.
        loser = result_b if result_a["acquired"] else result_a
        assert loser["error"] is not None
        assert "example_managed_repo" in loser["error"]

    def test_sequential_acquires_succeed(self, tmp_path: Path) -> None:
        """After A releases, B acquires successfully — no leftover lock files."""
        out_a = tmp_path / "a.json"
        out_b = tmp_path / "b.json"
        oc_src = _oc_src_dir()

        proc_a = _run_competitor(tmp_path, "example_managed_repo", 0.1, out_a, oc_src)
        proc_a.wait(timeout=10)
        # A has fully released by now.
        proc_b = _run_competitor(tmp_path, "example_managed_repo", 0.1, out_b, oc_src)
        proc_b.wait(timeout=10)

        result_a = json.loads(out_a.read_text())
        result_b = json.loads(out_b.read_text())
        assert result_a["acquired"] is True
        assert result_b["acquired"] is True
