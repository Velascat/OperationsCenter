# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Regression coverage for `classify_capacity_exhaustion` against real captures.

`tests/unit/backends/test_capacity_classifier.py` covers the synthetic
phrases the classifier was originally written against. This file pins
real-shape stdout from `tests/fixtures/backends/capacity_exhaustion/`
so the classifier can't silently regress on the in-the-wild form.

Adding a new fixture: drop the file under
`tests/fixtures/backends/capacity_exhaustion/<name>.stdout.txt` and
extend `KNOWN_FIXTURES` below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operations_center.backends._capacity_classifier import classify_capacity_exhaustion


_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "backends" / "capacity_exhaustion"

KNOWN_FIXTURES = [
    ("claude_code_extra_usage.stdout.txt", "out of extra usage"),
]


@pytest.mark.parametrize("filename,expected_match_substring", KNOWN_FIXTURES)
def test_classifier_matches_real_capture(filename: str, expected_match_substring: str) -> None:
    fixture = _FIXTURES_DIR / filename
    assert fixture.exists(), f"missing fixture: {fixture}"
    captured = fixture.read_text(encoding="utf-8")

    excerpt = classify_capacity_exhaustion(captured)
    assert excerpt is not None, (
        f"classifier missed real capacity-exhaustion capture {filename!r}; "
        f"if the wire format changed, update _CAPACITY_PATTERNS — do not relax this test."
    )
    assert expected_match_substring.lower() in excerpt.lower()


def test_fixture_directory_is_authoritative() -> None:
    """Every fixture file must be exercised by KNOWN_FIXTURES."""
    files_on_disk = sorted(p.name for p in _FIXTURES_DIR.glob("*.stdout.txt"))
    files_referenced = sorted(name for name, _ in KNOWN_FIXTURES)
    assert files_on_disk == files_referenced, (
        "tests/fixtures/backends/capacity_exhaustion/ contains files that aren't "
        "wired into KNOWN_FIXTURES — see this file's docstring."
    )
