from __future__ import annotations

import subprocess
from collections import defaultdict

from control_plane.adapters.github_pr import GitHubPRClient
from control_plane.observer.models import CICheckRunRecord, CIHistorySignal
from control_plane.observer.service import ObserverContext

_COMMIT_LOOKBACK = 5
_MAX_RUN_RECORDS = 50
_FLAKY_THRESHOLD = 0.2   # check is flaky if failure rate is between this and FAILING threshold
_FAILING_THRESHOLD = 0.7  # check is "consistently failing" above this rate


class CIHistoryCollector:
    """Fetch GitHub check-run history for recent commits and surface CI health signals."""

    def collect(self, context: ObserverContext) -> CIHistorySignal:
        token = context.settings.repo_git_token(context.repo_name)
        if not token:
            return CIHistorySignal(status="unavailable", source="no_git_token")

        repo_settings = context.settings.repos.get(context.repo_name)
        if not repo_settings:
            return CIHistorySignal(status="unavailable", source="repo_not_configured")

        try:
            owner, repo = GitHubPRClient.owner_repo_from_clone_url(repo_settings.clone_url)
        except ValueError:
            return CIHistorySignal(status="unavailable", source="clone_url_parse_error")

        recent_shas = self._get_recent_shas(context)
        if not recent_shas:
            return CIHistorySignal(status="unavailable", source="no_recent_commits")

        client = GitHubPRClient(token=token)
        all_records: list[CICheckRunRecord] = []

        for sha in recent_shas:
            try:
                runs = client.get_check_runs(owner, repo, sha)
            except Exception:
                continue
            for run in runs:
                conclusion = run.get("conclusion") or "pending"
                all_records.append(
                    CICheckRunRecord(
                        name=str(run.get("name", "unknown")),
                        sha=sha,
                        conclusion=conclusion,
                    )
                )

        if not all_records:
            return CIHistorySignal(status="unavailable", source="no_check_runs")

        # Aggregate per check name
        check_conclusions: dict[str, list[str]] = defaultdict(list)
        for record in all_records:
            check_conclusions[record.name].append(record.conclusion)

        failed_conclusions = {"failure", "timed_out", "cancelled"}
        flaky_checks: list[str] = []
        failing_checks: list[str] = []
        total_runs = 0
        total_failures = 0

        for check_name, conclusions in check_conclusions.items():
            n = len(conclusions)
            failures = sum(1 for c in conclusions if c in failed_conclusions)
            total_runs += n
            total_failures += failures
            if n == 0:
                continue
            rate = failures / n
            if rate >= _FAILING_THRESHOLD:
                failing_checks.append(check_name)
            elif rate >= _FLAKY_THRESHOLD:
                flaky_checks.append(check_name)

        overall_failure_rate = total_failures / total_runs if total_runs > 0 else 0.0

        if failing_checks:
            status = "failing"
        elif flaky_checks:
            status = "flaky"
        else:
            status = "nominal"

        return CIHistorySignal(
            status=status,
            runs_checked=len(recent_shas),
            failure_rate=round(overall_failure_rate, 3),
            flaky_checks=sorted(flaky_checks),
            failing_checks=sorted(failing_checks),
            recent_runs=all_records[:_MAX_RUN_RECORDS],
            source="github_checks",
        )

    @staticmethod
    def _get_recent_shas(context: ObserverContext) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "log", "--format=%H", f"-n{_COMMIT_LOOKBACK}"],
                cwd=context.repo_path,
                capture_output=True,
                text=True,
                timeout=15,
            )
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        except Exception:
            return []
