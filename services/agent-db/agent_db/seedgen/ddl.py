"""DDL generation: config entities → CREATE TABLE statements.

Replicates data-service/internal/seedgen/config_schema.go (GenerateDDL)
in Python. Same semantics: entities → CREATE TABLE with FK handling,
composite PK, driver-specific quoting and type mapping.
"""

from __future__ import annotations


from .models import Entity, EntityField, FieldType, RelationKind


def _quote_ident(name: str, driver: str) -> str:
    """Quote identifier in driver-specific syntax.

    Both SQLite and PostgreSQL use double quotes.
    Kept as function for future portability (e.g. MySQL backticks).
    """
    return f'"{name}"'


def _field_to_sql(field: EntityField, driver: str) -> str:
    """Map EntityField to SQL column type."""
    t = field.type
    if t == FieldType.STRING:
        sql_type = "TEXT"
    elif t == FieldType.INT:
        sql_type = "INTEGER"
    elif t == FieldType.FLOAT:
        sql_type = "REAL"
    elif t == FieldType.BOOL:
        sql_type = "BOOLEAN" if driver == "postgres" else "INTEGER"
    elif t == FieldType.JSON:
        sql_type = "JSONB" if driver == "postgres" else "TEXT"
    elif t in (FieldType.DATETIME, FieldType.DATE):
        sql_type = "TEXT"  # ISO 8601
    else:
        sql_type = "TEXT"

    col_def = f"{_quote_ident(field.column, driver)} {sql_type}"
    if field.nullable is not None and not field.nullable:
        col_def += " NOT NULL"
    return col_def


def generate_ddl(entities: list[Entity], driver: str = "sqlite") -> str:
    """Generate CREATE TABLE DDL from a list of Entity descriptions.

    Handles:
    - Composite primary keys (single PRIMARY KEY (c1, c2) clause)
    - Single flagged PK → PRIMARY KEY ("col")
    - Fallback to id_column if no field is flagged
    - FK: inline REFERENCES for SQLite, ALTER TABLE for Postgres
    - Driver-specific type mapping and identifier quoting

    Returns multi-statement DDL with statements separated by ``;\\n``.
    """
    if not entities:
        raise ValueError("generate_ddl: at least one entity required")

    stmts: list[str] = []
    fk_stmts: list[str] = []

    for ent in entities:
        if not ent.table or not ent.fields:
            continue

        # Collect FK references per column
        # key: local FK column name, value: "REFERENCES target_table(target_column)"
        fk_per_column: dict[str, str] = {}
        for rel in ent.relations or []:
            if rel.kind != RelationKind.MANY_TO_ONE:
                continue
            ref = f"REFERENCES {_quote_ident(rel.table, driver)}({_quote_ident(rel.target_fk, driver)})"
            if driver == "sqlite":
                fk_per_column[rel.local_fk] = ref
            else:
                # Postgres: ALTER TABLE after CREATE
                fk_name = f"fk_{ent.table}_{rel.field}"
                alter = (
                    f"ALTER TABLE {_quote_ident(ent.table, driver)} "
                    f"ADD CONSTRAINT {_quote_ident(fk_name, driver)} "
                    f"FOREIGN KEY ({_quote_ident(rel.local_fk, driver)}) "
                    f"REFERENCES {_quote_ident(rel.table, driver)}({_quote_ident(rel.target_fk, driver)})"
                )
                fk_stmts.append(alter)

        # Build columns
        cols: list[str] = []
        pk_cols: list[str] = []

        for field in ent.fields:
            col_def = _field_to_sql(field, driver)

            # Embed FK reference for SQLite
            if field.column in fk_per_column:
                col_def += " " + fk_per_column[field.column]

            # Collect PK-flagged columns for composite PK clause
            if field.primary_key:
                pk_cols.append(_quote_ident(field.column, driver))

            cols.append(col_def)

        # Emit PRIMARY KEY clause
        if pk_cols:
            cols.append(f"PRIMARY KEY ({', '.join(pk_cols)})")
        elif ent.id_column:
            # Fallback: no field flagged → use id_column
            cols.append(f"PRIMARY KEY ({_quote_ident(ent.id_column, driver)})")

        create = (
            f"CREATE TABLE IF NOT EXISTS {_quote_ident(ent.table, driver)} (\n"
            f"  {',\n  '.join(cols)}\n"
            f")"
        )
        stmts.append(create)

    stmts.extend(fk_stmts)
    return ";\n".join(stmts) + ";"
