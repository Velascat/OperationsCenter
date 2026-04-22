"""Tests for canonical backend factory cutover behavior."""

from __future__ import annotations

from types import SimpleNamespace

from control_plane.backends import factory as backend_factory


def test_canonical_registry_does_not_thread_switchboard_proxy_transport(monkeypatch) -> None:
    captured: list[tuple[str, str]] = []

    class FakeKodoAdapter:
        @classmethod
        def from_settings(cls, *, settings, switchboard_url: str = "", kodo_mode: str = "goal"):
            captured.append(("kodo", switchboard_url))
            return object()

    class FakeDirectLocalAdapter:
        def __init__(self, settings, switchboard_url: str = "") -> None:
            captured.append(("direct_local", switchboard_url))

    monkeypatch.setattr(backend_factory, "KodoBackendAdapter", FakeKodoAdapter)
    monkeypatch.setattr(backend_factory, "DirectLocalBackendAdapter", FakeDirectLocalAdapter)

    settings = SimpleNamespace(
        kodo=object(),
        aider=object(),
        spec_director=SimpleNamespace(switchboard_url="http://sb:20401"),
    )

    backend_factory.CanonicalBackendRegistry.from_settings(settings)

    assert captured == [("kodo", ""), ("direct_local", "")]
