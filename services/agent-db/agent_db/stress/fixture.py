"""Argument fixtures: where a tool call gets its values (doc/stress/README.md §3).

Verified live on 2026-09-27: ``/mcp/manifest`` publishes only ``name``,
``display_name``, ``endpoint`` and ``description``. There is no argument
metadata in it — the JSON schema an MCP client sees is assembled by the gateway
from the tenant config (``mcp_tools[].params``) when the tool is registered. So
the harness cannot synthesise arguments from the manifest, and a stage that
calls a tool without them measures the error path instead of the platform:
``db_get`` with a missing record answers ``isError: true`` in ~5 ms.

A fixture is bound to the seed scenario whose rows its values reference, and is
deliberately separate from the profile: several profiles run the same seeded
data, and the fixture must not be re-derived per ladder stage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .profile import LoadProfile, ProfileValidationError

FIXTURE_VERSION = 1

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

ToolArguments = dict[str, dict[str, Any]]


class FixtureValidationError(ProfileValidationError):
    """An argument fixture cannot be used with its profile."""


class ArgumentFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixture_version: int
    name: str = Field(min_length=1)
    # The seed scenario these values were taken from. A stage that runs this
    # fixture against different rows is a different workload, not a re-run.
    scenario: str = Field(min_length=1)
    arguments: dict[str, dict[str, Any]] = Field(min_length=1)

    @model_validator(mode="after")
    def _version_is_supported(self) -> ArgumentFixture:
        if self.fixture_version != FIXTURE_VERSION:
            raise ValueError(
                f"fixture_version must be {FIXTURE_VERSION}, got {self.fixture_version}"
            )
        return self

    def tools(self) -> list[str]:
        return list(self.arguments)


def load_fixture(name_or_path: str | Path) -> ArgumentFixture:
    """Load a fixture by name from ``fixtures/`` or by explicit path."""
    candidate = Path(name_or_path)
    path = (
        candidate if candidate.suffix == ".json" else FIXTURES_DIR / f"{candidate}.json"
    )
    if not path.exists():
        available = sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))
        raise FixtureValidationError(
            f"argument fixture {name_or_path!r} not found at {path}; available: "
            f"{available or 'none'}"
        )
    try:
        return ArgumentFixture.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except ValueError as exc:
        raise FixtureValidationError(f"{path}: {exc}") from exc


def validate_fixture_covers(profile: LoadProfile, fixture: ArgumentFixture) -> None:
    """Refuse a profile whose workload has a tool the fixture cannot feed.

    Coverage is checked against :meth:`LoadProfile.called_tools`, so the
    platform's per-turn schema preload counts too: it is not in the profile but
    it is in every turn, and a fixture that feeds only the declared tools would
    fail on the first turn instead of during preflight.

    Extra fixture entries are allowed on purpose: one fixture is shared by
    several profiles and may carry arguments for tools a given profile never
    calls.
    """
    missing = [name for name in profile.called_tools() if name not in fixture.arguments]
    if missing:
        raise FixtureValidationError(
            f"fixture {fixture.name!r} has no arguments for "
            f"{', '.join(repr(name) for name in missing)} used by profile "
            f"{profile.name!r}"
        )


def effective_fixture(fixture: ArgumentFixture, profile: LoadProfile) -> ToolArguments:
    """Arguments one turn of this profile uses, preload first.

    The order matches the calls a turn makes, so a driver can consume the two
    without re-deriving anything.
    """
    return {name: fixture.arguments[name] for name in profile.called_tools()}
