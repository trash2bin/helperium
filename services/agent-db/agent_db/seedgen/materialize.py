"""Materialize: create a fully populated SQLite database from a scenario.

A scenario is a directory containing:
- config.json  (entities, endpoints, data_source config)
- seed.json    (domain seed data — optional for schema-only scenarios)

Usage::

    from agent_db.seedgen import materialize

    # Materialize a scenario into an SQLite database file
    materialize.materialize("agent-db/scenarios/shop", force=True)

This replicates agent-db/core/__init__.py orchestration logic
(data-service/internal/seedgen/materialize.go) in pure Python.
"""

from __future__ import annotations

import json as _json
import logging
import os
import os.path
import sqlite3
from typing import Optional

from .ddl import generate_ddl
from .apply import apply_with_ddl
from .models import (
    ScenarioConfig,
    DataSourceConfig,
    Entity,
    EntityField,
    Relation,
    Seed,
)

logger = logging.getLogger(__name__)


def _load_config(path: str) -> ScenarioConfig:
    """Load and parse a scenario config.json."""
    with open(path, "r") as f:
        raw = _json.load(f)

    ds = raw.get("data_source", {})
    entities_raw = raw.get("entities", [])

    entities = []
    for e in entities_raw:
        fields = []
        for f in e.get("fields", []):
            fields.append(
                EntityField(
                    name=f["name"],
                    column=f["column"],
                    type=f.get("type", "string"),
                    nullable=f.get("nullable"),
                    primary_key=f.get("primary_key"),
                    description=f.get("description", ""),
                )
            )

        relations = []
        for r in e.get("relations", []):
            relations.append(
                Relation(
                    field=r["field"],
                    kind=r.get("kind", "many_to_one"),
                    table=r.get("table", ""),
                    local_fk=r.get("local_fk", ""),
                    target_fk=r.get("target_fk", ""),
                )
            )

        entities.append(
            Entity(
                name=e["name"],
                table=e["table"],
                id_column=e.get("id_column", ""),
                fields=fields,
                relations=relations,
                description=e.get("description", ""),
            )
        )

    return ScenarioConfig(
        version=raw.get("version", 1),
        data_source=DataSourceConfig(
            driver=ds.get("driver", "sqlite"),
            dsn=ds.get("dsn", ""),
        ),
        entities=entities,
    )


def _load_seed(path: str) -> Optional[Seed]:
    """Load a seed.json file. Returns None if file doesn't exist."""
    if not os.path.isfile(path):
        return None
    from helperium_sdk.seed_models import StorageSeed

    with open(path, "r") as f:
        raw = _json.load(f)
    return StorageSeed.model_validate(raw)


def _resolve_dsn(dsn: str, base_dir: str, driver: str) -> str:
    """Resolve a DSN from scenario config.

    If DSN is relative and driver is sqlite (not :memory:), make it absolute
    relative to base_dir.
    """
    if driver != "sqlite":
        return dsn
    if dsn in (":memory:", "") or dsn.startswith(":memory:?"):
        return dsn
    if os.path.isabs(dsn):
        return dsn
    return os.path.normpath(os.path.join(base_dir, dsn))


def _remove_db(path: str) -> None:
    """Remove SQLite database file and associated WAL/SHM files."""
    if os.path.isfile(path):
        os.remove(path)
    for suffix in ("-wal", "-shm"):
        p = path + suffix
        if os.path.isfile(p):
            os.remove(p)


def materialize(
    scenario_dir: str,
    force: bool = False,
    output_dsn: Optional[str] = None,
) -> ScenarioConfig:
    """Create a fully populated SQLite database from a scenario directory.

    Args:
        scenario_dir: Path to scenario directory containing config.json and
                      optional seed.json.
        force: If True, delete existing database before recreating.
        output_dsn: Override DSN (e.g. /tmp/my.db). If None, uses DSN from config.

    Returns:
        The parsed ScenarioConfig (with resolved DSN after materialization).

    Raises:
        FileNotFoundError: If config.json is missing.
        ValueError: If config has no entities or invalid driver.
    """
    abs_dir = os.path.abspath(scenario_dir)
    config_path = os.path.join(abs_dir, "config.json")

    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"config.json not found in {scenario_dir}")

    cfg = _load_config(config_path)
    driver = cfg.data_source.driver

    if driver not in ("sqlite", "postgres"):
        raise ValueError(f"unsupported driver: {driver}")

    if not cfg.entities:
        raise ValueError("config has no entities — nothing to materialize")

    # Resolve DSN
    if output_dsn:
        dsn = output_dsn
    else:
        dsn = _resolve_dsn(cfg.data_source.dsn, abs_dir, driver)

    # Remove existing DB if force
    if force and driver == "sqlite":
        _remove_db(dsn)

    # Load seed
    seed_path = os.path.join(abs_dir, "seed.json")
    seed = _load_seed(seed_path)
    if seed is not None:
        logger.info(
            "Loaded seed: %d groups, %d students", len(seed.groups), len(seed.students)
        )
    else:
        logger.info("No seed.json — schema only")

    # Generate DDL from entities
    ddl = generate_ddl(cfg.entities, driver)
    logger.info("Generated DDL for %d entities", len(cfg.entities))

    # Connect to database
    if driver == "sqlite":
        conn = sqlite3.connect(dsn)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    else:
        raise ValueError(
            "PostgreSQL materialization not yet implemented in Python seedgen"
        )

    try:
        apply_with_ddl(conn, ddl=ddl, seed=seed, driver=driver)
        conn.commit()
    finally:
        conn.close()

    # Update config with resolved DSN
    cfg.data_source.dsn = dsn

    logger.info("Materialize complete: %s", dsn)
    return cfg
