# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Velascat
"""Sample scrubber tests."""
from __future__ import annotations

from operations_center.executors._scrub import REDACTED, scrub_sample, scrub_text


class TestTokenRedaction:
    def test_openai_anthropic_sk_token(self):
        out = scrub_text("api key is sk-abcdef0123456789xyz blah")
        assert REDACTED in out
        assert "sk-abcdef0123456789xyz" not in out

    def test_anthropic_sk_ant_token(self):
        out = scrub_text("Authorization: Bearer sk-ant-abc123def456ghi789jklm")
        assert REDACTED in out

    def test_github_pat_token(self):
        out = scrub_text("token=ghp_AbCdEfGhIjKlMnOpQrStUv12345 done")
        assert REDACTED in out
        assert "ghp_AbCdEfGhIj" not in out

    def test_aws_access_key(self):
        out = scrub_text("AWS_KEY=AKIAIOSFODNN7EXAMPLE")
        assert REDACTED in out

    def test_short_string_with_no_secret_unchanged(self):
        assert scrub_text("hello world") == "hello world"


class TestPathRedaction:
    def test_home_path_replaced(self):
        out = scrub_text("read /home/alice/secrets.txt")
        assert "/home/alice" not in out
        assert "/<USER_HOME>" in out

    def test_macos_users_path_replaced(self):
        out = scrub_text("read /Users/bob/file.txt")
        assert "/Users/bob" not in out


class TestKeyRedaction:
    def test_dict_credential_keys_redacted(self):
        payload = {
            "api_key": "anything-goes",
            "password": "swordfish",
            "secret": "keep-this-out",
            "model": "opus",
        }
        out = scrub_sample(payload)
        assert out["api_key"] == REDACTED
        assert out["password"] == REDACTED
        assert out["secret"] == REDACTED
        assert out["model"] == "opus"  # not credential-keyed

    def test_nested_dict_keys_redacted(self):
        payload = {
            "config": {"access_token": "live-prod-key", "endpoint": "https://x"},
            "ok": True,
        }
        out = scrub_sample(payload)
        assert out["config"]["access_token"] == REDACTED
        assert out["config"]["endpoint"] == "https://x"
        assert out["ok"] is True

    def test_list_of_dicts_scrubbed_per_element(self):
        payload = {"events": [{"bearer": "abc"}, {"step": 1}]}
        out = scrub_sample(payload)
        assert out["events"][0]["bearer"] == REDACTED
        assert out["events"][1]["step"] == 1


class TestPrimitivePassthrough:
    def test_int_unchanged(self):
        assert scrub_sample(42) == 42

    def test_none_unchanged(self):
        assert scrub_sample(None) is None

    def test_bool_unchanged(self):
        assert scrub_sample(True) is True
        assert scrub_sample(False) is False

    def test_string_with_no_secret_unchanged(self):
        assert scrub_sample("hello world") == "hello world"
