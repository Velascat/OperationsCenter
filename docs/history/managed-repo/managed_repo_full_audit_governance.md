# Managed Repo Full Audit Governance

## Purpose

Phase 12 adds explicit governance around when full managed audits may run.

Full audits are expensive, disruptive, and require real managed repo infrastructure. Governance ensures they run only when there is a clear reason, authorized by an identified operator, within defined resource limits.

The mini regression suite (Phase 11) remains the default fast feedback path. Full audits are exceptional, governed, and report-driven.

---

## Relationship to Phase 6 Dispatch

Phase 12 wraps Phase 6 dispatch. It never bypasses it.

```
AuditGovernanceRequest
    ↓
Phase 12: run_governed_audit(request, approval?) → AuditGovernedRunResult
    ↓  (only when decision == "approved" and approval valid)
Phase 6: dispatch_managed_audit(request) → ManagedAuditDispatchResult
```

Phase 6 is only called when:
1. All required policy checks pass.
2. The governance decision is `approved`.
3. Manual approval is provided if `requires_manual_approval=True`.

---

## Relationship to Phase 11 Mini Regression

Phase 12 enforces the **mini regression first** principle as a governance policy.

For `urgency=low` or `urgency=normal`: a related mini regression suite report is required before a full audit is approved. Without it, the decision is `needs_manual_approval`.

For `urgency=high` or `urgency=urgent`: no mini regression evidence is still a warning, but urgent requests always require manual approval regardless.

This ensures the fast feedback loop (Phase 9–11) is not bypassed as a matter of habit.

---

## Mini Regression First Rule

Before requesting a full audit, operators should run a Phase 11 mini regression suite and include `related_suite_report_path` in the governance request.

```bash
# 1. Run mini regression
operations-center-regression run \
    --suite examples/mini_regression/basic_fixture_integrity_suite.json \
    --output-dir tools/audit/report/mini_regression

# 2. Include the report path in the governance request
operations-center-governance request \
    --repo managed-private-project \
    --type representative \
    --reason "fixture refresh after pipeline change" \
    --requested-by alice \
    --suite-report tools/audit/report/mini_regression/basic_fixture_integrity/suite_run_id/suite_report.json \
    --output /tmp/governance_request.json
```

---

## Governance Request Model

`AuditGovernanceRequest` (Pydantic frozen):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `request_id` | `str` | auto | Path-safe unique ID |
| `repo_id` | `str` | — | Managed repo ID |
| `audit_type` | `str` | — | Audit type |
| `requested_by` | `str` | — | Non-empty operator identity |
| `requested_reason` | `str` | — | Non-empty explanation |
| `urgency` | `"low"\|"normal"\|"high"\|"urgent"` | `"normal"` | |
| `created_at` | `datetime` | auto UTC | |
| `related_suite_report_path` | `str\|None` | `None` | Evidence for mini-regression-first policy |
| `related_calibration_report_path` | `str\|None` | `None` | Context only |
| `related_recommendation_ids` | `list[str]` | `[]` | Context only — cannot approve |
| `allow_if_recent_success` | `bool` | `False` | |
| `metadata` | `dict` | `{}` | |

---

## Governance Decision Model

`AuditGovernanceDecision` (Pydantic frozen):

| Field | Notes |
|-------|-------|
| `decision_id` | Auto UUID |
| `request_id` | References AuditGovernanceRequest |
| `decision` | `"approved"\|"denied"\|"needs_manual_approval"\|"deferred"` |
| `reasons` | List of human-readable explanations |
| `policy_results` | All PolicyResult objects from evaluation |
| `requires_manual_approval` | True when decision=needs_manual_approval |
| `approved_by` | Set by manual approval flow |

### Decision rules

| Condition | Decision |
|-----------|----------|
| known_repo, known_audit_type, or manual_request_required fails | `denied` |
| urgency=high/urgent | `needs_manual_approval` |
| mini_regression_first fails (low/normal urgency) | `needs_manual_approval` |
| budget/cooldown fails + high/urgent urgency | `needs_manual_approval` |
| budget/cooldown fails + low/normal urgency | `deferred` |
| All required checks pass, low/normal urgency | `approved` |

---

## Policy Checks

Eight policy checks run in order for every governance request:

| Policy | Failure → Decision |
|--------|--------------------|
| `manual_request_required` | requested_by empty → `denied` |
| `known_repo_required` | repo_id not in known_repos → `denied` |
| `known_audit_type_required` | audit_type not configured → `denied` |
| `cooldown_policy` | cooldown active → `deferred` or `needs_manual_approval` |
| `budget_policy` | budget exhausted → `deferred` or `needs_manual_approval` |
| `mini_regression_first_policy` | no suite report + low/normal urgency → `needs_manual_approval` |
| `urgent_override_policy` | urgency=high/urgent → `needs_manual_approval` (warning) |
| `recent_success_policy` | recent run in cooldown window → advisory warning |

Policy results have status: `passed | failed | warning | skipped`

---

## Budget and Cooldown Tracking

### Budget

`AuditBudgetState` tracks how many full audits may run within a rolling period.

```
BudgetConfig:
  max_runs: int = 10          # maximum runs per period
  period_days: int = 7        # rolling period length
```

State is file-backed JSON at `{state_dir}/{repo_id}__{audit_type}__budget.json`.

Period rolls over automatically when `period_end` is exceeded.
Budget state is updated only after a successful dispatch.

### Cooldown

`AuditCooldownState` enforces a minimum gap between consecutive full audits.

```
CooldownConfig:
  cooldown_seconds: float = 3600.0   # 1 hour default
```

State is file-backed JSON at `{state_dir}/{repo_id}__{audit_type}__cooldown.json`.

Cooldown state is updated only after a successful dispatch call.

### State directory

```
tools/audit/governance/state/
  managed-private-project__representative__budget.json
  managed-private-project__representative__cooldown.json
```

State is OperationsCenter-owned. Managed repo code does not read or write it.

---

## Manual Approval Artifact

`AuditManualApproval` (Pydantic frozen) is a data artifact representing human sign-off.

| Field | Notes |
|-------|-------|
| `approval_id` | Auto UUID |
| `decision_id` | Must match the decision being approved |
| `request_id` | Must match the original request |
| `approved_by` | Non-empty human operator name |
| `approved_at` | Auto UTC |
| `approval_notes` | Optional context |

Manual approval:
- Is a data artifact, not code execution
- Does not bypass `known_repo_required` or `known_audit_type_required`
- Is validated before dispatch is called
- Is recorded in the governance report

---

## Governed Audit Runner

`run_governed_audit(request, *, approval=None, ...)` is the single safe entry point.

```python
from operations_center.audit_governance import (
    AuditGovernanceRequest,
    GovernanceConfig,
    run_governed_audit,
)

request = AuditGovernanceRequest(
    repo_id="managed-private-project",
    audit_type="representative",
    requested_by="alice",
    requested_reason="fixture refresh after pipeline change",
    urgency="normal",
    related_suite_report_path="tools/audit/report/mini_regression/.../suite_report.json",
)

cfg = GovernanceConfig(
    known_repos=["managed-private-project"],
    known_audit_types={"managed-private-project": ["representative", "enrichment"]},
)

result = run_governed_audit(request, governance_config=cfg)
# result.governance_status == "approved_and_dispatched" | "denied" | "deferred" | "needs_manual_approval"
```

### Governance status values

| Status | Meaning |
|--------|---------|
| `approved_and_dispatched` | Governance passed, Phase 6 was called |
| `denied` | A hard policy check failed; dispatch not called |
| `deferred` | Budget/cooldown failed; retry later; dispatch not called |
| `needs_manual_approval` | Manual approval required; dispatch not called |
| `dispatch_failed` | Governance approved but Phase 6 raised; dispatch may have partially executed |

---

## Governance Reports

Every governance request produces a report — even denied or deferred ones.

`AuditGovernanceReport` (Pydantic):

| Field | Notes |
|-------|-------|
| `request` | Full AuditGovernanceRequest |
| `decision` | Full AuditGovernanceDecision with all policy results |
| `policy_results` | Ordered list of PolicyResult |
| `approval` | AuditManualApproval if provided |
| `dispatch_result_summary` | Compact dispatch outcome if dispatched |
| `budget_state_summary` | Budget snapshot at evaluation time |
| `cooldown_state_summary` | Cooldown snapshot at evaluation time |

Report path: `{output_dir}/{repo_id}/{audit_type}/{request_id}/governance_report.json`

Default output root: `tools/audit/report/governance/`

---

## Recommendation Boundary

Calibration recommendations (Phase 8) are context-only in governance.

```
related_recommendation_ids: list[str]   # IDs only — no objects, no code references
```

Rules:
- Recommendations may be referenced by ID in a governance request.
- Recommendations may not approve a governance request.
- Recommendations may not call dispatch.
- Recommendations may not mutate governance state.
- A human operator must create the governance request and approval separately.

This preserves the anti-collapse invariant from Phase 8.

---

## CLI / Tool Entry Points

```bash
# Create a governance request JSON
operations-center-governance request \
    --repo managed-private-project \
    --type representative \
    --reason "fixture refresh after pipeline change" \
    --requested-by alice \
    --urgency normal \
    --suite-report tools/audit/report/mini_regression/.../suite_report.json \
    --output /tmp/request.json

# Evaluate the request against policies (no dispatch)
operations-center-governance evaluate \
    --request /tmp/request.json \
    --known-repos managed-private-project \
    --known-types representative,enrichment

# Approve a decision that requires manual sign-off
operations-center-governance approve \
    --request /tmp/request.json \
    --decision /tmp/decision.json \
    --approved-by alice \
    --notes "Post-incident fixture refresh approved in ops meeting." \
    --output /tmp/approval.json

# Run with approval
operations-center-governance run \
    --request /tmp/request.json \
    --approval /tmp/approval.json \
    --known-repos managed-private-project \
    --known-types representative,enrichment

# Inspect a governance report
operations-center-governance inspect \
    --report tools/audit/report/governance/managed-private-project/representative/<request_id>/governance_report.json
```

Exit codes:
- `0` — governance approved and dispatched (or approved without dispatch issue)
- `1` — denied or dispatch failed
- `2` — needs_manual_approval or deferred
- `3` — approval validation failed

---

## Non-Goals

Phase 12 explicitly does **not**:

- Replace Phase 9–11 (fixture harvesting, slice replay, mini regression)
- Automatically run mini regression before a full audit
- Implement daemon/watch loops or schedulers
- Auto-apply calibration recommendations
- Mutate producer artifacts
- Import managed repo code
- Bypass governance to call dispatch directly
- Implement self-tuning policy changes
- Implement CI gate integration (future phase)

---

## Module Layout

```
src/operations_center/audit_governance/
    __init__.py      — public exports
    errors.py        — AuditGovernanceError hierarchy
    models.py        — All governance models and configuration dataclasses
    policy.py        — evaluate_governance_policies(), make_governance_decision()
    approvals.py     — validate_manual_approval(), make_manual_approval()
    budgets.py       — Budget state load/save/increment
    cooldowns.py     — Cooldown state load/save/update
    runner.py        — run_governed_audit()
    reports.py       — write_governance_report(), load_governance_report()

src/operations_center/entrypoints/governance/
    main.py          — operations-center-governance CLI (Typer)

tools/audit/governance/state/
    {repo_id}__{audit_type}__budget.json
    {repo_id}__{audit_type}__cooldown.json

tools/audit/report/governance/
    {repo_id}/{audit_type}/{request_id}/governance_report.json
```
