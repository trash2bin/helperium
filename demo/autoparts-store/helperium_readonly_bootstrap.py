#!/usr/bin/env python3
"""Provision a PostgreSQL-enforced read-only Helperium tenant for autoparts.

This process is intentionally one-shot and idempotent. It receives the
storefront writer credential only to create/maintain a separate database role,
then registers the tenant through data-service's authenticated admin API. It
never logs passwords or DSNs.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import psycopg2
from psycopg2 import sql

CATALOG_TABLES = (
    "catalog_product",
    "catalog_brand",
    "catalog_category",
    "catalog_order",
    "catalog_cart",
    "catalog_cartitem",
    "catalog_sitesettings",
)


@dataclass(frozen=True)
class BootstrapSettings:
    store_host: str
    store_port: int
    store_database: str
    store_user: str
    store_password: str
    readonly_user: str
    readonly_password: str
    register_tenant: bool
    data_service_url: str
    data_admin_token: str
    tenant_id: str
    attempts: int
    retry_seconds: float


def required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def load_settings() -> BootstrapSettings:
    register_tenant = os.environ.get("HELPERIUM_AUTOPARTS_REGISTER_TENANT", "false").lower() in {"1", "true", "yes"}
    return BootstrapSettings(
        store_host=os.environ.get("STORE_DB_HOST", "storefront-db"),
        store_port=int(os.environ.get("STORE_DB_PORT", "5432")),
        store_database=required_env("STORE_DB_NAME"),
        store_user=required_env("STORE_DB_USER"),
        store_password=required_env("STORE_DB_PASSWORD"),
        readonly_user=os.environ.get("HELPERIUM_AUTOPARTS_RO_USER", "helperium_autoparts_ro"),
        readonly_password=required_env("HELPERIUM_AUTOPARTS_RO_PASSWORD"),
        register_tenant=register_tenant,
        data_service_url=required_env("HELPERIUM_DATA_SERVICE_URL").rstrip("/") if register_tenant else "",
        data_admin_token=required_env("HELPERIUM_DATA_ADMIN_TOKEN") if register_tenant else "",
        tenant_id=os.environ.get("HELPERIUM_AUTOPARTS_TENANT_ID", "autoparts"),
        attempts=int(os.environ.get("HELPERIUM_BOOTSTRAP_ATTEMPTS", "60")),
        retry_seconds=float(os.environ.get("HELPERIUM_BOOTSTRAP_RETRY_SECONDS", "2")),
    )


def table_list() -> sql.Composed:
    return sql.SQL(", ").join(
        sql.SQL("public.{}").format(sql.Identifier(table)) for table in CATALOG_TABLES
    )


def catalog_tables_ready(cursor: Any) -> bool:
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = ANY(%s)
        """,
        (list(CATALOG_TABLES),),
    )
    return {row[0] for row in cursor.fetchall()} == set(CATALOG_TABLES)


def provision_readonly_role(settings: BootstrapSettings) -> bool:
    """Return False until Django migrations and seed schema are visible."""
    connection = psycopg2.connect(
        host=settings.store_host,
        port=settings.store_port,
        dbname=settings.store_database,
        user=settings.store_user,
        password=settings.store_password,
        connect_timeout=5,
    )
    try:
        with connection:
            with connection.cursor() as cursor:
                if not catalog_tables_ready(cursor):
                    return False

                role = sql.Identifier(settings.readonly_user)
                database = sql.Identifier(settings.store_database)
                tables = table_list()

                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.readonly_user,))
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %s"
                        ).format(role),
                        (settings.readonly_password,),
                    )
                else:
                    cursor.execute(
                        sql.SQL(
                            "ALTER ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %s"
                        ).format(role),
                        (settings.readonly_password,),
                    )

                cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(database, role))
                # PostgreSQL grants TEMPORARY to PUBLIC by default. Revoke it on
                # the dedicated demo database so the Helperium login has only
                # the explicit CONNECT, schema USAGE and table SELECT grants.
                cursor.execute(sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(database))
                cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role))
                cursor.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(role))
                cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
                cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON TABLE {} FROM {}").format(tables, role))
                cursor.execute(sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {}").format(role))
                cursor.execute(
                    sql.SQL(
                        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER "
                        "ON TABLE {} FROM PUBLIC"
                    ).format(tables)
                )
                cursor.execute(sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(tables, role))
    finally:
        connection.close()
    return True


def readonly_dsn(settings: BootstrapSettings) -> str:
    username = quote(settings.readonly_user, safe="")
    password = quote(settings.readonly_password, safe="")
    database = quote(settings.store_database, safe="")
    return f"postgres://{username}:{password}@{settings.store_host}:{settings.store_port}/{database}?sslmode=disable"


def request_json(
    settings: BootstrapSettings,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    tenant_scoped: bool = False,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {settings.data_admin_token}",
        "Content-Type": "application/json",
        "User-Agent": "helperium-autoparts-readonly-bootstrap/1",
    }
    if tenant_scoped:
        headers["X-Tenant-ID"] = settings.tenant_id
    request = Request(
        f"{settings.data_service_url}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"message": "non-json response"}
        return error.code, payload


def bootstrap_tenant(settings: BootstrapSettings) -> None:
    config = {
        "version": 1,
        "data_source": {
            "driver": "postgres",
            "dsn": readonly_dsn(settings),
            "read_only": True,
        },
        "introspection": {"enabled": True, "include_schemas": ["public"]},
        "entities": [],
        "endpoints": [],
    }
    status, _ = request_json(settings, "GET", f"/admin/tenants/{settings.tenant_id}")
    if status == 404:
        status, response = request_json(
            settings,
            "POST",
            "/admin/tenants",
            {"id": settings.tenant_id, "config": config},
        )
        action = "registered"
    elif status == 200:
        status, response = request_json(
            settings,
            "POST",
            "/admin/config",
            config,
            tenant_scoped=True,
        )
        action = "updated"
    else:
        raise RuntimeError(f"tenant lookup returned HTTP {status}")

    if status not in {200, 201}:
        message = response.get("message") or response.get("error") or "unknown error"
        raise RuntimeError(f"tenant {action} request returned HTTP {status}: {message}")

    status, response = request_json(settings, "POST", "/admin/config/rewrite", tenant_scoped=True)
    if status != 200:
        message = response.get("message") or response.get("error") or "unknown error"
        raise RuntimeError(f"tenant rewrite returned HTTP {status}: {message}")


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as error:
        print(f"autoparts readonly bootstrap configuration error: {error}", file=sys.stderr)
        return 2

    for attempt in range(1, settings.attempts + 1):
        try:
            if not provision_readonly_role(settings):
                raise RuntimeError("catalog schema is not ready")
            if settings.register_tenant:
                bootstrap_tenant(settings)
            mode = "role-and-tenant" if settings.register_tenant else "role-only"
            print(
                "autoparts readonly bootstrap complete "
                f"mode={mode} tenant={settings.tenant_id} role={settings.readonly_user} "
                f"attempt={attempt}"
            )
            return 0
        except (OSError, RuntimeError, psycopg2.Error, URLError) as error:
            if attempt == settings.attempts:
                print(
                    "autoparts readonly bootstrap failed after "
                    f"{settings.attempts} attempts: {error}",
                    file=sys.stderr,
                )
                return 1
            print(
                "autoparts readonly bootstrap waiting "
                f"attempt={attempt}/{settings.attempts}: {error}",
                file=sys.stderr,
            )
            time.sleep(settings.retry_seconds)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
