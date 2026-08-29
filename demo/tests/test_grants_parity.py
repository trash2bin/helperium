"""Grants parity guard for the autoparts read-only bootstrap.

The one-shot bootstrap grants SELECT on CATALOG_TABLES from
demo/autoparts-store/helperium_readonly_bootstrap.py, while the seeded schema
it grants against lives in demo/autoparts-store/db/schema.sql. Both lists are
hand-maintained: a table added to the schema without extending the grants
tuple leaves the Helperium read-only role unable to query it (silent data
gap), and a grant for a renamed table fails at bootstrap time. These offline
tests pin the two lists to each other and verify the comparison itself fires
on drift in both directions. No database and no docker are involved.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_SQL = ROOT / "demo" / "autoparts-store" / "db" / "schema.sql"
BOOTSTRAP = ROOT / "demo" / "autoparts-store" / "helperium_readonly_bootstrap.py"

_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:\"([^\"]+)\"|([A-Za-z_][\w]*)(?:\.([A-Za-z_][\w]*))?)",
    re.IGNORECASE,
)


def _parse_schema_tables(sql_text: str) -> set[str]:
    """Return bare, lowercased table names from CREATE TABLE statements.

    Line comments are stripped first so commented-out DDL stays inert, and
    CREATE INDEX statements must not count: they reference tables via ON and
    appear once per index, not per new table.
    """
    without_comments = re.sub(r"--[^\n]*", "", sql_text)
    names: set[str] = set()
    for match in _CREATE_TABLE.finditer(without_comments):
        quoted, first, second = match.groups()
        raw = quoted or second or first
        names.add(raw.rsplit(".", 1)[-1].lower())
    return names


def _assert_same_tables(granted: set[str], seeded: set[str]) -> None:
    """Raise with a both-sides diff unless the grants tuple matches the schema."""
    missing_grants = sorted(seeded - granted)
    unknown_grants = sorted(granted - seeded)
    if missing_grants or unknown_grants:
        raise AssertionError(
            "CATALOG_TABLES grants drift vs demo schema: "
            f"tables in schema.sql without a SELECT grant: {missing_grants}; "
            f"grants referencing tables absent from schema.sql: {unknown_grants}"
        )


@pytest.fixture
def bootstrap_module(monkeypatch):
    """Load the bootstrap script without a real psycopg2 dependency.

    Mirrors the stubbing pattern in demo/tests/unit/test_autoparts_readonly_bootstrap.py.
    """
    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2.Error = Exception
    fake_psycopg2.connect = lambda **_: None
    fake_sql = types.ModuleType("psycopg2.sql")
    fake_sql.Composed = object
    fake_sql.Identifier = lambda value: value
    fake_sql.SQL = lambda value: value
    fake_psycopg2.sql = fake_sql
    monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.sql", fake_sql)

    spec = importlib.util.spec_from_file_location(
        "autoparts_readonly_bootstrap_parity", BOOTSTRAP
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _seeded_tables() -> set[str]:
    return _parse_schema_tables(SCHEMA_SQL.read_text(encoding="utf-8"))


def test_schema_sql_is_present_and_parses_nonempty():
    assert SCHEMA_SQL.is_file(), f"seed DDL moved: {SCHEMA_SQL} not found"
    assert _seeded_tables(), (
        "parser found no CREATE TABLE statements; "
        "the parity guard below would pass vacuously"
    )


def test_grants_tuple_matches_seeded_schema(bootstrap_module):
    _assert_same_tables(set(bootstrap_module.CATALOG_TABLES), _seeded_tables())


def test_guard_reports_seed_tables_without_grant(bootstrap_module):
    granted = set(bootstrap_module.CATALOG_TABLES) - {"catalog_order"}
    with pytest.raises(AssertionError) as excinfo:
        _assert_same_tables(granted, _seeded_tables())
    message = str(excinfo.value)
    assert "catalog_order" in message
    assert "without a SELECT grant" in message


def test_guard_reports_grants_for_unknown_tables(bootstrap_module):
    granted = set(bootstrap_module.CATALOG_TABLES) | {"catalog_promobanner"}
    with pytest.raises(AssertionError) as excinfo:
        _assert_same_tables(granted, _seeded_tables())
    message = str(excinfo.value)
    assert "catalog_promobanner" in message
    assert "absent from schema.sql" in message


def test_parser_ignores_indexes_and_comments():
    sql_text = (
        "-- CREATE TABLE catalog_commented_out (id INT);\n"
        "CREATE TABLE catalog_brand (id BIGINT);\n"
        "CREATE INDEX catalog_brand_name_idx ON catalog_brand(name);\n"
        "CREATE TABLE IF NOT EXISTS public.catalog_extra (id BIGINT);\n"
    )
    assert _parse_schema_tables(sql_text) == {"catalog_brand", "catalog_extra"}
