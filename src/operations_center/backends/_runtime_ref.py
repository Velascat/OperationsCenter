# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Helper for building ``ExecutionResult.runtime_invocation_ref``.

Adapters that delegate execution mechanics to ExecutorRuntime call
``runtime_invocation_ref(invocation, rxp_result)`` and pass the result
into ``ExecutionResult(...)``. This is the canonical place where the OC
↔ RxP linkage is captured.
"""

from __future__ import annotations

from typing import Optional

from rxp.contracts import RuntimeInvocation, RuntimeResult

from operations_center.contracts.execution import RuntimeInvocationRef


def runtime_invocation_ref(
    invocation: RuntimeInvocation,
    rxp_result: Optional[RuntimeResult] = None,
) -> RuntimeInvocationRef:
    """Build a RuntimeInvocationRef from an RxP invocation (and optional result).

    The invocation alone provides the stable identity (invocation_id,
    runtime_name, runtime_kind, artifact_directory). When the runner
    returned a RuntimeResult, stdout_path / stderr_path are pulled from
    it.
    """
    stdout_path = getattr(rxp_result, "stdout_path", None) if rxp_result is not None else None
    stderr_path = getattr(rxp_result, "stderr_path", None) if rxp_result is not None else None
    return RuntimeInvocationRef(
        invocation_id=invocation.invocation_id,
        runtime_name=invocation.runtime_name,
        runtime_kind=invocation.runtime_kind,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        artifact_directory=invocation.artifact_directory,
    )
