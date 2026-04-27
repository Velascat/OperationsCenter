"""Tests for OpsCenter architecture invariant rules.

Each rule family gets:
  - a clean-pass test (valid code → no findings)
  - one or more violation tests (bad code → expected findings)

The live-repo integration tests at the bottom run the full checker against
the actual src/operations_center/ directory and assert zero findings.
"""
from __future__ import annotations

from pathlib import Path


from tools.audit.architecture_invariants.baseline import (
    compare_to_baseline,
    load_baseline,
    save_baseline,
)
from tools.audit.architecture_invariants.check_architecture_invariants import run_audit
from tools.audit.architecture_invariants.import_rules import check_managed_repo_imports
from tools.audit.architecture_invariants.invariant_models import AuditReport, Finding, Severity, Status
from tools.audit.architecture_invariants.layer_rules import check_layer_direction
from tools.audit.architecture_invariants.mutation_rules import check_anti_collapse_guardrail
from tools.audit.architecture_invariants.scanning_rules import check_no_directory_scanning


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_src(tmp_path: Path, rel: str, src: str) -> Path:
    """Write a Python file under tmp_path/src/operations_center/."""
    full = tmp_path / "src" / "operations_center" / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(src, encoding="utf-8")
    return full


# ===========================================================================
# 1 — import_rules: check_managed_repo_imports
# ===========================================================================

class TestImportRules:
    def test_clean_file_passes(self, tmp_path: Path):
        _write_src(tmp_path, "foo/bar.py", "import os\nfrom pathlib import Path\n")
        findings = check_managed_repo_imports(tmp_path)
        assert findings == []

    def test_absolute_videofoundry_import_fails(self, tmp_path: Path):
        _write_src(tmp_path, "foo/bar.py", "import videofoundry\n")
        findings = check_managed_repo_imports(tmp_path)
        assert len(findings) == 1
        assert findings[0].family == "managed_repo_import"
        assert findings[0].status == Status.FAIL
        assert "videofoundry" in findings[0].evidence

    def test_from_videofoundry_import_fails(self, tmp_path: Path):
        _write_src(tmp_path, "foo/bar.py", "from videofoundry.core import Thing\n")
        findings = check_managed_repo_imports(tmp_path)
        assert len(findings) == 1
        assert "videofoundry" in findings[0].evidence

    def test_tools_audit_import_fails(self, tmp_path: Path):
        _write_src(tmp_path, "foo/bar.py", "from tools.audit.contracts import run_status\n")
        findings = check_managed_repo_imports(tmp_path)
        assert len(findings) == 1
        assert "tools.audit" in findings[0].evidence

    def test_relative_import_from_videofoundry_module_passes(self, tmp_path: Path):
        """from .videofoundry import X is a relative import — not a VF package import."""
        _write_src(tmp_path, "audit_contracts/profiles/__init__.py",
                   "from .videofoundry import VideoFoundryProducerProfile\n")
        findings = check_managed_repo_imports(tmp_path)
        assert findings == []

    def test_multiple_violations_reported(self, tmp_path: Path):
        _write_src(tmp_path, "foo/a.py", "import videofoundry\n")
        _write_src(tmp_path, "foo/b.py", "import managed_repo\n")
        findings = check_managed_repo_imports(tmp_path)
        assert len(findings) == 2

    def test_no_src_dir_returns_empty(self, tmp_path: Path):
        findings = check_managed_repo_imports(tmp_path)
        assert findings == []


# ===========================================================================
# 2 — layer_rules: check_layer_direction
# ===========================================================================

class TestLayerRules:
    def _make_pkg(self, tmp_path: Path, pkg: str, src: str) -> None:
        _write_src(tmp_path, f"{pkg}/__init__.py", "")
        _write_src(tmp_path, f"{pkg}/module.py", src)

    def test_clean_imports_pass(self, tmp_path: Path):
        self._make_pkg(tmp_path, "slice_replay", "from operations_center.fixture_harvesting import x\n")
        findings = check_layer_direction(tmp_path)
        assert findings == []

    def test_slice_replay_imports_dispatch_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "slice_replay",
            "from operations_center.audit_dispatch import dispatch_managed_audit\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any(f.family == "layer_direction" for f in findings)
        assert any("DISPATCH-ISO-SR" in f.id for f in findings)

    def test_mini_regression_imports_dispatch_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "mini_regression",
            "from operations_center.audit_dispatch.api import dispatch\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any("DISPATCH-ISO-MR" in f.id for f in findings)

    def test_fixture_harvesting_imports_dispatch_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "fixture_harvesting",
            "import operations_center.audit_dispatch\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any("DISPATCH-ISO-FH" in f.id for f in findings)

    def test_governance_imports_fixture_harvesting_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "audit_governance",
            "from operations_center.fixture_harvesting import harvest_fixtures\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any("GOV-ISO-FH" in f.id for f in findings)

    def test_governance_imports_slice_replay_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "audit_governance",
            "from operations_center.slice_replay import run_slice_replay\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any("GOV-ISO-SR" in f.id for f in findings)

    def test_governance_imports_mini_regression_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "audit_governance",
            "from operations_center.mini_regression import run_mini_regression_suite\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any("GOV-ISO-MR" in f.id for f in findings)

    def test_mini_regression_imports_governance_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "mini_regression",
            "from operations_center.audit_governance import run_governed_audit\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any("REPLAY-STACK-MR" in f.id for f in findings)

    def test_slice_replay_imports_mini_regression_fails(self, tmp_path: Path):
        self._make_pkg(
            tmp_path, "slice_replay",
            "from operations_center.mini_regression import something\n",
        )
        findings = check_layer_direction(tmp_path)
        assert any("REPLAY-STACK-SR2" in f.id for f in findings)

    def test_governance_importing_dispatch_is_fine(self, tmp_path: Path):
        """audit_governance → audit_dispatch is the one allowed upward edge."""
        self._make_pkg(
            tmp_path, "audit_governance",
            "from operations_center.audit_dispatch import dispatch_managed_audit\n",
        )
        findings = check_layer_direction(tmp_path)
        assert findings == []

    def test_missing_pkg_dir_skipped(self, tmp_path: Path):
        """Rules for packages that don't exist yet should not error."""
        findings = check_layer_direction(tmp_path)
        assert findings == []


# ===========================================================================
# 3 — scanning_rules: check_no_directory_scanning
# ===========================================================================

class TestScanningRules:
    def _write_index(self, tmp_path: Path, name: str, src: str) -> None:
        _write_src(tmp_path, f"artifact_index/{name}", src)

    def test_clean_file_passes(self, tmp_path: Path):
        self._write_index(tmp_path, "loader.py", "import json\ndef load(path): return json.loads(path.read_text())\n")
        findings = check_no_directory_scanning(tmp_path)
        assert findings == []

    def test_path_glob_fails(self, tmp_path: Path):
        self._write_index(tmp_path, "bad.py", "files = list(some_dir.glob('*.json'))\n")
        findings = check_no_directory_scanning(tmp_path)
        assert len(findings) == 1
        assert findings[0].family == "no_scanning"
        assert ".glob(" in findings[0].evidence

    def test_path_rglob_fails(self, tmp_path: Path):
        self._write_index(tmp_path, "bad.py", "for f in root.rglob('artifact_manifest.json'): pass\n")
        findings = check_no_directory_scanning(tmp_path)
        assert len(findings) == 1
        assert ".rglob(" in findings[0].evidence

    def test_os_scandir_fails(self, tmp_path: Path):
        self._write_index(tmp_path, "bad.py", "import os\nfor entry in os.scandir(path): pass\n")
        findings = check_no_directory_scanning(tmp_path)
        assert len(findings) == 1
        assert ".scandir(" in findings[0].evidence

    def test_os_walk_fails(self, tmp_path: Path):
        self._write_index(tmp_path, "bad.py", "import os\nfor r, d, f in os.walk(root): pass\n")
        findings = check_no_directory_scanning(tmp_path)
        assert len(findings) == 1
        assert ".walk(" in findings[0].evidence

    def test_os_listdir_fails(self, tmp_path: Path):
        self._write_index(tmp_path, "bad.py", "import os\nentries = os.listdir(p)\n")
        findings = check_no_directory_scanning(tmp_path)
        assert len(findings) == 1

    def test_no_artifact_index_dir_returns_empty(self, tmp_path: Path):
        findings = check_no_directory_scanning(tmp_path)
        assert findings == []

    def test_multiple_violations_in_one_file(self, tmp_path: Path):
        src = "root.glob('*')\nroot.rglob('*.json')\n"
        self._write_index(tmp_path, "bad.py", src)
        findings = check_no_directory_scanning(tmp_path)
        assert len(findings) == 2


# ===========================================================================
# 4 — mutation_rules: check_anti_collapse_guardrail
# ===========================================================================

class TestMutationRules:
    def _write_guardrails(self, tmp_path: Path, src: str) -> None:
        _write_src(tmp_path, "behavior_calibration/guardrails.py", src)

    def test_clean_guardrail_passes(self, tmp_path: Path):
        src = (
            "from frozenset import *\n"
            "_FORBIDDEN_MUTATION_FIELDS = frozenset({'auto_apply', 'execute', 'mutate'})\n"
        )
        self._write_guardrails(tmp_path, src)
        findings = check_anti_collapse_guardrail(tmp_path)
        assert findings == []

    def test_missing_guardrails_file_fails(self, tmp_path: Path):
        (tmp_path / "src" / "operations_center" / "behavior_calibration").mkdir(
            parents=True, exist_ok=True
        )
        findings = check_anti_collapse_guardrail(tmp_path)
        assert any(f.family == "anti_collapse" for f in findings)
        assert any("missing" in f.message.lower() for f in findings)

    def test_empty_forbidden_fields_fails(self, tmp_path: Path):
        self._write_guardrails(tmp_path, "_FORBIDDEN_MUTATION_FIELDS = frozenset(set())\n")
        findings = check_anti_collapse_guardrail(tmp_path)
        assert any("missing or empty" in f.message for f in findings)

    def test_missing_auto_apply_fails(self, tmp_path: Path):
        self._write_guardrails(tmp_path, "_FORBIDDEN_MUTATION_FIELDS = frozenset({'execute'})\n")
        findings = check_anti_collapse_guardrail(tmp_path)
        assert any("auto_apply" in f.message for f in findings)

    def test_model_with_forbidden_field_fails(self, tmp_path: Path):
        guardrail_src = "_FORBIDDEN_MUTATION_FIELDS = frozenset({'auto_apply', 'execute'})\n"
        self._write_guardrails(tmp_path, guardrail_src)
        _write_src(
            tmp_path,
            "behavior_calibration/models.py",
            "class MyRec:\n    auto_apply: bool = False\n",
        )
        findings = check_anti_collapse_guardrail(tmp_path)
        assert any("auto_apply" in f.message for f in findings)
        assert any(f.family == "anti_collapse" for f in findings)

    def test_clean_model_passes(self, tmp_path: Path):
        guardrail_src = "_FORBIDDEN_MUTATION_FIELDS = frozenset({'auto_apply', 'execute'})\n"
        self._write_guardrails(tmp_path, guardrail_src)
        _write_src(
            tmp_path,
            "behavior_calibration/models.py",
            "class CalibrationRecommendation:\n    description: str\n    severity: str\n",
        )
        findings = check_anti_collapse_guardrail(tmp_path)
        assert findings == []


# ===========================================================================
# 5 — baseline: save / load / compare
# ===========================================================================

class TestBaseline:
    def _finding_dict(self, path: str = "src/foo.py", line: int = 1) -> dict:
        return {
            "id": "OC-ARCH-IMPORT-001",
            "family": "managed_repo_import",
            "severity": "fail",
            "status": "fail",
            "path": path,
            "line": line,
            "message": "test",
            "evidence": "import videofoundry",
            "suggested_fix": "remove it",
        }

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        f = self._finding_dict()
        path = tmp_path / "baseline.json"
        save_baseline([f], path)
        loaded = load_baseline(path)
        assert loaded == [f]

    def test_compare_no_changes(self, tmp_path: Path):
        f = self._finding_dict()
        result = compare_to_baseline([f], [f])
        assert result.new_count == 0
        assert result.resolved_count == 0
        assert result.existing_count == 1

    def test_compare_new_finding(self, tmp_path: Path):
        old = self._finding_dict(path="src/a.py")
        new_f = self._finding_dict(path="src/b.py")
        result = compare_to_baseline([old], [old, new_f])
        assert result.new_count == 1
        assert result.new_findings[0]["path"] == "src/b.py"
        assert result.resolved_count == 0

    def test_compare_resolved_finding(self, tmp_path: Path):
        f = self._finding_dict()
        result = compare_to_baseline([f], [])
        assert result.resolved_count == 1
        assert result.new_count == 0

    def test_compare_empty_both(self, tmp_path: Path):
        result = compare_to_baseline([], [])
        assert result.new_count == 0
        assert result.resolved_count == 0
        assert result.existing_count == 0


# ===========================================================================
# 6 — AuditReport model
# ===========================================================================

class TestAuditReport:
    def _finding(self, status: Status) -> Finding:
        return Finding(
            id="X-001", family="test", severity=Severity.FAIL,
            status=status, path="a.py", line=1,
            message="msg", evidence="ev", suggested_fix="fix",
        )

    def test_overall_status_pass(self):
        report = AuditReport(repo_root="/repo", findings=[])
        assert report.overall_status() == "pass"

    def test_overall_status_fail(self):
        report = AuditReport(repo_root="/repo", findings=[self._finding(Status.FAIL)])
        assert report.overall_status() == "fail"

    def test_overall_status_warn_when_no_fail(self):
        report = AuditReport(repo_root="/repo", findings=[self._finding(Status.WARN)])
        assert report.overall_status() == "warn"

    def test_fail_dominates_warn(self):
        report = AuditReport(
            repo_root="/repo",
            findings=[self._finding(Status.WARN), self._finding(Status.FAIL)],
        )
        assert report.overall_status() == "fail"

    def test_to_json_is_valid(self):
        import json
        report = AuditReport(repo_root="/repo", findings=[])
        data = json.loads(report.to_json())
        assert data["status"] == "pass"
        assert "findings" in data
        assert "summary" in data

    def test_summary_counts(self):
        report = AuditReport(
            repo_root="/repo",
            findings=[self._finding(Status.FAIL), self._finding(Status.WARN)],
        )
        counts = report.summary_counts()
        assert counts["fail"] == 1
        assert counts["warn"] == 1


# ===========================================================================
# 7 — Live integration: run_audit against the actual OpsCenter src tree
# ===========================================================================

class TestLiveRepoClean:
    """Run the full checker against the real codebase — expect zero failures."""

    _REPO_ROOT = Path(__file__).parents[3]  # OperationsCenter/

    def test_no_managed_repo_imports_in_src(self):
        findings = check_managed_repo_imports(self._REPO_ROOT)
        assert findings == [], (
            "Managed repo import violations found:\n"
            + "\n".join(f"  {f.path}:{f.line} — {f.message}" for f in findings)
        )

    def test_no_layer_direction_violations(self):
        findings = check_layer_direction(self._REPO_ROOT)
        assert findings == [], (
            "Layer direction violations found:\n"
            + "\n".join(f"  {f.path}:{f.line} — {f.message}" for f in findings)
        )

    def test_no_directory_scanning_in_artifact_index(self):
        findings = check_no_directory_scanning(self._REPO_ROOT)
        assert findings == [], (
            "Directory scanning violations found:\n"
            + "\n".join(f"  {f.path}:{f.line} — {f.evidence}" for f in findings)
        )

    def test_anti_collapse_guardrail_intact(self):
        findings = check_anti_collapse_guardrail(self._REPO_ROOT)
        assert findings == [], (
            "Anti-collapse guardrail violations found:\n"
            + "\n".join(f"  {f.path}:{f.line} — {f.message}" for f in findings)
        )

    def test_full_audit_passes(self):
        report = run_audit(self._REPO_ROOT)
        fail_findings = [f for f in report.findings if f.status == Status.FAIL]
        assert fail_findings == [], (
            "Architecture invariant violations:\n"
            + "\n".join(f"  [{f.id}] {f.path}:{f.line} — {f.message}" for f in fail_findings)
        )
