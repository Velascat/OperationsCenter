# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Executor framework — backend discovery, normalization, gaps, cards, verdicts.

Per-backend layout (see docs/architecture/audit/backend_control_audit.md):

    operations_center/executors/<backend>/
      adapter.py             # discovery harness — invoke + capture
      normalizer.py          # raw → CxRP ExecutionResult
      samples/raw_output/    # captured backend output (scrubbed)
      samples/invocations/   # invocation metadata (scrubbed)
      contract_gaps.yaml     # justification trail for forks
      capability_card.yaml   # objective advertised capabilities
      runtime_support.yaml   # objective supported runtime kinds
      audit_verdict.yaml     # final mechanical verdict
      recommendations.md     # subjective commentary
"""
