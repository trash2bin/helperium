"""Stress/capacity harness for Helperium (design: doc/stress/README.md).

Status: phase 1 in progress. This package currently carries the contract layer
(profile schema, raw request record, percentile calculator). The driver,
scheduler, substrate and report are not written yet, and no number produced from
this package is publishable until they are.
"""

from __future__ import annotations

from .fixture import (
    ArgumentFixture,
    FixtureValidationError,
    effective_fixture,
    load_fixture,
    validate_fixture_covers,
)
from .profile import (
    LoadProfile,
    ProfileValidationError,
    canonical_tool_name,
    load_profile,
    validate_tools_against_manifest,
)
from .records import (
    ErrorClass,
    RawRequestRecord,
    StageStats,
    percentile,
    summarise,
    write_raw_records,
)

__all__ = [
    "ArgumentFixture",
    "ErrorClass",
    "FixtureValidationError",
    "LoadProfile",
    "ProfileValidationError",
    "RawRequestRecord",
    "StageStats",
    "canonical_tool_name",
    "effective_fixture",
    "load_fixture",
    "load_profile",
    "percentile",
    "summarise",
    "validate_fixture_covers",
    "validate_tools_against_manifest",
    "write_raw_records",
]
