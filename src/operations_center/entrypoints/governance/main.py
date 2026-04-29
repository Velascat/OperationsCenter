# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Velascat
"""CLI entry point: operations-center-governance.

Commands
--------
  request   Create a governance request JSON from CLI arguments.
  evaluate  Evaluate a governance request file against policies.
  approve   Create a manual approval artifact for a governance decision.
  run       Run a governed audit (evaluate + dispatch if approved).
  inspect   Inspect a previously written governance report.

This CLI does NOT:
  - bypass governance to call dispatch directly
  - apply calibration recommendations
  - mutate producer artifacts
  - import managed repo code
  - implement scheduling or watch loops
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from typing import cast

from operations_center.audit_governance import (
    AuditGovernanceRequest,
    AuditManualApproval,
    GovernanceConfig,
    GovernanceReportError,
    evaluate_governance_policies,
    load_governance_report,
    make_governance_decision,
    make_manual_approval,
    run_governed_audit,
)
from operations_center.audit_governance.models import AuditUrgency

app = typer.Typer(
    help="Full audit governance — approve, deny, and dispatch managed repo audits.",
    no_args_is_help=True,
)
console = Console()

_DEFAULT_OUTPUT_DIR = "tools/audit/report/governance"


def _status_color(decision: str) -> str:
    return {
        "approved": "green",
        "approved_and_dispatched": "green",
        "denied": "red",
        "deferred": "yellow",
        "needs_manual_approval": "cyan",
        "dispatch_failed": "red",
    }.get(decision, "white")


@app.command("request")
def cmd_request(
    repo: str = typer.Option(..., "--repo", "-r", help="Managed repo ID (e.g. 'videofoundry')."),
    audit_type: str = typer.Option(..., "--type", "-t", help="Audit type."),
    reason: str = typer.Option(..., "--reason", help="Why the full audit is needed."),
    requested_by: str = typer.Option(..., "--requested-by", help="Operator identity."),
    urgency: str = typer.Option("normal", "--urgency", help="low|normal|high|urgent"),
    suite_report: str | None = typer.Option(None, "--suite-report", help="Related mini regression report path."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write request JSON to file."),
) -> None:
    """Create a governance request JSON."""
    try:
        req = AuditGovernanceRequest(
            repo_id=repo,
            audit_type=audit_type,
            requested_by=requested_by,
            requested_reason=reason,
            urgency=cast(AuditUrgency, urgency),
            related_suite_report_path=suite_report,
        )
    except Exception as exc:
        console.print(f"[red]Invalid request:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    payload = req.model_dump_json(indent=2)
    if output:
        Path(output).write_text(payload, encoding="utf-8")
        console.print(f"Request written to [bold]{output}[/bold]  (request_id={req.request_id})")
    else:
        console.print(payload)


@app.command("evaluate")
def cmd_evaluate(
    request_file: str = typer.Option(..., "--request", help="Path to governance request JSON."),
    known_repos: str = typer.Option("", "--known-repos", help="Comma-separated known repo IDs."),
    known_types: str = typer.Option("", "--known-types", help="Comma-separated audit types (applied to all repos)."),
) -> None:
    """Evaluate a governance request against policy checks."""
    try:
        data = json.loads(Path(request_file).read_text(encoding="utf-8"))
        request = AuditGovernanceRequest.model_validate(data)
    except FileNotFoundError:
        console.print(f"[red]Not found:[/red] {request_file}")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]Cannot load request:[/red] {exc}")
        raise typer.Exit(code=2)

    repos = [r.strip() for r in known_repos.split(",") if r.strip()]
    types_list = [t.strip() for t in known_types.split(",") if t.strip()]
    audit_types_map = {request.repo_id: types_list} if types_list else {}

    cfg = GovernanceConfig(known_repos=repos, known_audit_types=audit_types_map)
    policy_results = evaluate_governance_policies(
        request,
        known_repos=cfg.known_repos,
        known_audit_types=cfg.known_audit_types,
    )
    decision = make_governance_decision(request, policy_results)

    color = _status_color(decision.decision)
    console.print("[bold]Governance Evaluation[/bold]")
    console.print(f"  repo:     {request.repo_id}")
    console.print(f"  type:     {request.audit_type}")
    console.print(f"  urgency:  {request.urgency}")
    console.print(f"  decision: [{color}]{decision.decision.upper()}[/{color}]")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Policy", overflow="fold")
    table.add_column("Status", width=10)
    table.add_column("Reason", overflow="fold")
    for p in policy_results:
        pc = "green" if p.status == "passed" else "red" if p.status == "failed" else "yellow"
        table.add_row(p.policy_name, f"[{pc}]{p.status}[/{pc}]", p.reason)
    console.print(table)

    if decision.decision in ("denied", "dispatch_failed"):
        raise typer.Exit(code=1)


@app.command("approve")
def cmd_approve(
    decision_file: str = typer.Option(..., "--decision", help="Path to governance decision JSON."),
    request_file: str = typer.Option(..., "--request", help="Path to governance request JSON."),
    approved_by: str = typer.Option(..., "--approved-by", help="Human operator name."),
    notes: str = typer.Option("", "--notes", help="Approval notes."),
    output: str | None = typer.Option(None, "--output", "-o", help="Write approval JSON to file."),
) -> None:
    """Create a manual approval artifact for a governance decision."""
    try:
        req_data = json.loads(Path(request_file).read_text(encoding="utf-8"))
        request = AuditGovernanceRequest.model_validate(req_data)
        dec_data = json.loads(Path(decision_file).read_text(encoding="utf-8"))
        from operations_center.audit_governance import AuditGovernanceDecision
        decision = AuditGovernanceDecision.model_validate(dec_data)
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]Cannot load files:[/red] {exc}")
        raise typer.Exit(code=2)

    try:
        approval = make_manual_approval(decision, request, approved_by=approved_by, approval_notes=notes)
    except Exception as exc:
        console.print(f"[red]Approval validation failed:[/red] {exc}")
        raise typer.Exit(code=3)

    payload = approval.model_dump_json(indent=2)
    if output:
        Path(output).write_text(payload, encoding="utf-8")
        console.print(f"Approval written to [bold]{output}[/bold]  (approval_id={approval.approval_id})")
    else:
        console.print(payload)


@app.command("run")
def cmd_run(
    request_file: str = typer.Option(..., "--request", help="Path to governance request JSON."),
    approval_file: str | None = typer.Option(None, "--approval", help="Path to manual approval JSON (if required)."),
    output_dir: str = typer.Option(_DEFAULT_OUTPUT_DIR, "--output-dir", "-o"),
    state_dir: str | None = typer.Option(None, "--state-dir", help="Override budget/cooldown state directory."),
    known_repos: str = typer.Option("", "--known-repos", help="Comma-separated known repo IDs."),
    known_types: str = typer.Option("", "--known-types", help="Comma-separated audit types."),
    timeout: float | None = typer.Option(None, "--timeout", help="Dispatch timeout in seconds."),
) -> None:
    """Evaluate governance and run the audit if approved."""
    try:
        data = json.loads(Path(request_file).read_text(encoding="utf-8"))
        request = AuditGovernanceRequest.model_validate(data)
    except FileNotFoundError:
        console.print(f"[red]Not found:[/red] {request_file}")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]Cannot load request:[/red] {exc}")
        raise typer.Exit(code=2)

    approval = None
    if approval_file:
        try:
            ap_data = json.loads(Path(approval_file).read_text(encoding="utf-8"))
            approval = AuditManualApproval.model_validate(ap_data)
        except Exception as exc:
            console.print(f"[red]Cannot load approval:[/red] {exc}")
            raise typer.Exit(code=2)

    repos = [r.strip() for r in known_repos.split(",") if r.strip()]
    types_list = [t.strip() for t in known_types.split(",") if t.strip()]
    cfg_kwargs: dict = dict(
        known_repos=repos,
        known_audit_types={request.repo_id: types_list} if types_list else {},
    )
    if state_dir:
        cfg_kwargs["state_dir"] = Path(state_dir)
    cfg = GovernanceConfig(**cfg_kwargs)

    result = run_governed_audit(
        request,
        approval=approval,
        governance_config=cfg,
        output_dir=output_dir,
        dispatch_timeout_seconds=timeout,
    )

    color = _status_color(result.governance_status)
    console.print(f"[{color}]{result.governance_status.upper()}[/{color}]")
    console.print(f"  decision:  {result.decision.decision}")
    if result.report_path:
        console.print(f"  report:    {result.report_path}")
    if result.dispatch_result:
        dr = result.dispatch_result
        console.print(f"  run_id:    {dr.run_id}")
        console.print(f"  status:    {dr.status.value}")
        if dr.error:
            console.print(f"  error:     {dr.error}")

    if result.governance_status in ("denied", "dispatch_failed"):
        raise typer.Exit(code=1)
    if result.governance_status in ("needs_manual_approval", "deferred"):
        raise typer.Exit(code=2)


@app.command("inspect")
def cmd_inspect(
    report: str = typer.Option(..., "--report", "-r", help="Path to governance_report.json."),
) -> None:
    """Inspect a previously written governance report."""
    try:
        rep = load_governance_report(Path(report))
    except FileNotFoundError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=1)
    except GovernanceReportError as exc:
        console.print(f"[red]Load error:[/red] {exc}")
        raise typer.Exit(code=2)

    dec = rep.decision
    color = _status_color(dec.decision)
    console.print("[bold]Governance Report[/bold]")
    console.print(f"  repo:         {rep.request.repo_id}")
    console.print(f"  audit_type:   {rep.request.audit_type}")
    console.print(f"  requested_by: {rep.request.requested_by}")
    console.print(f"  reason:       {rep.request.requested_reason}")
    console.print(f"  urgency:      {rep.request.urgency}")
    console.print(f"  decision:     [{color}]{dec.decision.upper()}[/{color}]")

    for reason in dec.reasons:
        console.print(f"    • {reason}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Policy", overflow="fold")
    table.add_column("Status", width=10)
    table.add_column("Reason", overflow="fold")
    for p in rep.policy_results:
        pc = "green" if p.status == "passed" else "red" if p.status == "failed" else "yellow"
        table.add_row(p.policy_name, f"[{pc}]{p.status}[/{pc}]", p.reason)
    console.print(table)

    if rep.dispatch_result_summary:
        dr = rep.dispatch_result_summary
        console.print("\n[bold]Dispatch[/bold]")
        console.print(f"  run_id:  {dr.run_id}")
        console.print(f"  status:  {dr.status}")
        if dr.error:
            console.print(f"  error:   {dr.error}")

    if rep.budget_state_summary:
        b = rep.budget_state_summary
        console.print(f"\n[bold]Budget[/bold]  {b.runs_used}/{b.max_runs} used ({b.runs_remaining} remaining)")

    if rep.cooldown_state_summary:
        c = rep.cooldown_state_summary
        in_cd = "YES" if c.in_cooldown else "no"
        console.print(f"[bold]Cooldown[/bold]  active={in_cd}  {c.seconds_remaining:.0f}s remaining")
