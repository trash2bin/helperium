"""seedgen — Python reimplementation of data-service/internal/seedgen.

Usage (from tests/e2e/helpers.py or CLI)::

    from agent_db.seedgen import materialize, generate_ddl, apply_with_ddl, apply, TestSeed

    # Generate a DB from a scenario
    cfg = materialize("agent-db/scenarios/shop", force=True)

    # Or use with an existing sqlite3 connection
    import sqlite3
    conn = sqlite3.connect(":memory:")
    apply(conn, TestSeed)
"""

from . import models
from . import ddl
from . import apply as _apply_module
from . import materialize as _materialize_module

from .ddl import generate_ddl
from .apply import apply_with_ddl, apply
from .materialize import materialize
from .models import TestSeed, Seed, ScenarioConfig

__all__ = [
    "generate_ddl",
    "apply_with_ddl",
    "apply",
    "materialize",
    "TestSeed",
    "Seed",
    "ScenarioConfig",
    "models",
    "ddl",
    "_apply_module",
    "_materialize_module",
]
