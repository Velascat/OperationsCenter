# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Card synthesizer tests."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from operations_center.executors.synthesizer import (
    OngoingSynthesizer,
    synthesize_from_samples,
    write_synthesized_diffs,
)


def _seed_sample(backend_dir: Path, name: str, raw: dict) -> None:
    raw_dir = backend_dir / "samples" / "raw_output"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{name}.json").write_text(json.dumps({
        "run_id": name,
        "lane": "coding_agent",
        "raw_output": raw,
    }))


class TestBatchSynthesizer:
    def test_empty_dir_returns_zero_facts(self, tmp_path):
        facts = synthesize_from_samples(tmp_path / "kodo")
        assert facts.sample_count == 0
        assert facts.observed_capabilities == set()

    def test_aggregates_across_samples(self, tmp_path):
        bd = tmp_path / "kodo"
        _seed_sample(bd, "s1", {"exit_code": 0, "files_changed": ["a", "b"], "tests_run": ["t1"]})
        _seed_sample(bd, "s2", {"exit_code": 0, "files_changed": ["a"], "commands_run": ["x", "y", "z"]})
        _seed_sample(bd, "s3", {"exit_code": 1, "stderr": "boom"})

        facts = synthesize_from_samples(bd)
        assert facts.sample_count == 3
        assert facts.success_count == 2
        assert facts.failure_count == 1
        assert facts.max_files_changed == 2
        assert facts.max_commands_run == 3
        assert facts.max_tests_run == 1
        assert "repo_patch" in facts.observed_capabilities
        assert "test_run" in facts.observed_capabilities
        assert "shell_write" in facts.observed_capabilities

    def test_capability_diff_only_adds(self, tmp_path):
        bd = tmp_path / "kodo"
        _seed_sample(bd, "s1", {"exit_code": 0, "files_changed": ["a"]})
        facts = synthesize_from_samples(bd)
        diff = facts.to_capability_card_diff(baseline={
            "advertised_capabilities": ["repo_read", "human_review"],
        })
        # baseline preserved, new caps added
        assert "repo_read" in diff["advertised_capabilities"]
        assert "human_review" in diff["advertised_capabilities"]
        assert "repo_patch" in diff["advertised_capabilities"]


class TestWriteDiffs:
    def test_writes_synthesized_yaml_files(self, tmp_path):
        bd = tmp_path / "kodo"
        _seed_sample(bd, "s1", {"exit_code": 0, "files_changed": ["a", "b"]})
        facts = synthesize_from_samples(bd)
        cap_path, rs_path = write_synthesized_diffs(bd, facts)
        assert cap_path.name == "capability_card.synthesized.yaml"
        assert rs_path.name == "runtime_support.synthesized.yaml"
        cap = yaml.safe_load(cap_path.read_text())
        assert cap["measured_constraints"]["max_observed_files_changed"] == 2
        assert cap["measured_constraints"]["sample_count"] == 1
        assert cap["measured_constraints"]["success_rate"] == 1.0


class TestOngoingSynthesizer:
    def test_observe_increments_facts(self, tmp_path):
        bd = tmp_path / "kodo"
        bd.mkdir()
        syn = OngoingSynthesizer(backend_dir=bd, flush_every_observations=100)
        syn.observe({"raw_output": {"exit_code": 0, "files_changed": ["a"]}})
        syn.observe({"raw_output": {"exit_code": 1, "stderr": "fail"}})
        assert syn.facts.sample_count == 2
        assert syn.facts.success_count == 1
        assert syn.facts.failure_count == 1

    def test_flush_on_observation_count(self, tmp_path):
        bd = tmp_path / "kodo"
        bd.mkdir()
        syn = OngoingSynthesizer(backend_dir=bd, flush_every_observations=2, flush_every_seconds=999)
        assert syn.observe({"raw_output": {"exit_code": 0}}) is False
        flushed = syn.observe({"raw_output": {"exit_code": 0}})
        assert flushed is True
        assert (bd / "capability_card.synthesized.yaml").exists()

    def test_explicit_flush(self, tmp_path):
        bd = tmp_path / "kodo"
        bd.mkdir()
        syn = OngoingSynthesizer(backend_dir=bd)
        syn.observe({"raw_output": {"exit_code": 0, "files_changed": ["a"]}})
        cap_path, _ = syn.flush()
        assert cap_path.exists()

    def test_carries_existing_samples_at_init(self, tmp_path):
        bd = tmp_path / "kodo"
        _seed_sample(bd, "preexisting", {"exit_code": 0, "files_changed": ["a"]})
        syn = OngoingSynthesizer(backend_dir=bd)
        # Pre-existing sample is loaded into facts at construction
        assert syn.facts.sample_count == 1
