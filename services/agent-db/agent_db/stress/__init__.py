"""Stress/capacity harness for Helperium (design: doc/stress/README.md).

Status: phase 1 in progress. This package currently carries the contract layer
(profile schema, raw request record, percentile calculator). The driver,
scheduler, substrate and report are not written yet, and no number produced from
this package is publishable until they are.
"""

from __future__ import annotations

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
    "ErrorClass",
    "LoadProfile",
    "ProfileValidationError",
    "RawRequestRecord",
    "StageStats",
    "canonical_tool_name",
    "load_profile",
    "percentile",
    "summarise",
    "validate_tools_against_manifest",
    "write_raw_records",
]
