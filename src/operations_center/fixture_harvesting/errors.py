"""Errors for the fixture harvesting subsystem."""

from __future__ import annotations


class FixtureHarvestingError(Exception):
    """Base error for the fixture harvesting subsystem."""


class HarvestInputError(FixtureHarvestingError):
    """Invalid or incomplete harvest request."""


class FixturePackWriteError(FixtureHarvestingError):
    """Failure writing a fixture pack to disk."""


class FixturePackLoadError(FixtureHarvestingError):
    """Failure loading or validating a fixture pack."""


class UnsafePathError(FixtureHarvestingError):
    """A resolved artifact path escapes the fixture pack directory."""


__all__ = [
    "FixtureHarvestingError",
    "HarvestInputError",
    "FixturePackWriteError",
    "FixturePackLoadError",
    "UnsafePathError",
]
