# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""ER-002 — Run Memory primitive.

File-backed, deterministic run memory index. Advisory only — never mutates
the current execution. Rebuildable from on-disk ``ExecutionResult`` artifacts.

Single write site:
  ``record_execution_result(result, index_dir)`` is the only function that
  appends a record. Callers in OperationsCenter invoke it after a result is
  finalized. No other module should write to the index.
"""

from .index import (
    RunMemoryIndexWriter,
    RunMemoryQueryService,
    deterministic_record_id,
    record_execution_result,
    rebuild_index_from_artifacts,
)
from .models import (
    RunMemoryQuery,
    RunMemoryRecord,
    SourceType,
)

__all__ = [
    "RunMemoryIndexWriter",
    "RunMemoryQueryService",
    "RunMemoryQuery",
    "RunMemoryRecord",
    "SourceType",
    "deterministic_record_id",
    "record_execution_result",
    "rebuild_index_from_artifacts",
]
