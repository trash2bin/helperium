"""
Full auto-generation pipeline for a foreign (non-university) database.

Usage:
    cd agent-tutor
    uv run python data-service/testdata/scripts/create_shop_db.py
    uv run python data-service/testdata/scripts/foreign_db_pipeline.py \
        --db data-service/testdata/scripts/shop.db --name shop

Pipeline:
   1. --discover → auto-generated config.json
   2. Parse schema → faker generates seed + DDL
   3. Insert seed into a NEW materialized database via Python
   4. Enrich config with custom_queries from FK relationships
   5. Start server → test all auto-generated endpoints

Phase 3.0-3.8 vision: "any db -> auto-discover -> auto-seed -> REST API"
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from shutil import which
from urllib.request import Request, urlopen

from faker import Faker

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_SERVICE = PROJECT_ROOT / "data-service"
_GO = which("go") or Path("/opt/homebrew/bin/go")
fake = Faker("ru_RU")


# ── discover / schema ──────────────────────────────────────────────────


def discover_config(db_path: Path) -> dict:
    """Run go run --discover against a foreign DB."""
    result = subprocess.run(
        [str(_GO), "run", "./data-service/cmd/server/", "--discover"],
        cwd=str(PROJECT_ROOT),
        env={"DB_PATH": str(db_path), "HOME": PROJECT_ROOT.parent},
        capture_output=True,
        text=True,
        timeout=30,
    )
    lines = result.stdout.strip().split("\n")
    json_start = next(i for i, l in enumerate(lines) if l.strip().startswith("{"))
    return json.loads("\n".join(lines[json_start:]))


def get_schema(db_path: Path) -> dict:
    """Extract {table: {columns, pks, fks}} from SQLite PRAGMA."""
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.execute("PRAGMA foreign_keys = ON")
    schema = {}
    for (tname,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        cols = db.execute(f"PRAGMA table_info({tname})").fetchall()
        fks = [
            {"from_col": f[3], "to_table": f[2], "to_col": f[4]}
            for f in db.execute(f"PRAGMA foreign_key_list({tname})")
        ]
        schema[tname] = {
            "columns": [(c[1], c[2], c[3]) for c in cols],
            "pks": [c[1] for c in cols if c[5]],
            "fks": fks,
        }
    db.close()
    return schema


# ── DDL + seed (Python, no --materialize) ──────────────────────────────


def _topo_sort(schema: dict) -> list[str]:
    """Sort tables so parents (FK targets) come before children."""
    tables = list(schema.keys())
    deps: dict[str, set[str]] = {t: set() for t in tables}
    for tname, info in schema.items():
        for fk in info["fks"]:
            deps[tname].add(fk["to_table"])
    # Kahn's algorithm
    result, remaining = [], list(tables)
    while remaining:
        ready = [
            t
            for t in remaining
            if not deps[t] or all(d not in remaining for d in deps[t])
        ]
        if not ready:
            result.extend(remaining)
            break
        for t in ready:
            result.append(t)
            remaining.remove(t)
    return result


def build_ddl(schema: dict) -> str:
    """Generate CREATE TABLE statements from schema dict (topo-sorted)."""
    ddl_parts = []
    for tname in _topo_sort(schema):
        info = schema[tname]
        cols = []
        pk_cols = set(info["pks"])
        for col_name, col_type, nullable in info["columns"]:
            typ = (
                "INTEGER"
                if ("INT" in col_type.upper() or col_type.upper() == "REAL")
                else "TEXT"
            )
            null_clause = "" if nullable is False or col_name in pk_cols else ""
            cols.append(f"    {col_name} {typ} {null_clause}".strip())
        # PK clause
        if info["pks"]:
            cols.append(f"    PRIMARY KEY ({', '.join(info['pks'])})")
        # FK clauses
        for fk in info["fks"]:
            cols.append(
                f"    FOREIGN KEY ({fk['from_col']}) REFERENCES {fk['to_table']}({fk['to_col']})"
            )
        ddl_parts.append(f"CREATE TABLE {tname} (\n" + ",\n".join(cols) + "\n);")
    return "\n".join(ddl_parts)


def _gen_value(col_name: str, col_type: str) -> object:
    """Faker value driven by column name + type."""
    cn = col_name.lower()
    # name hints
    if cn == "name":
        return fake.company() if random.random() > 0.5 else fake.catch_phrase()
    if cn in ("description", "comment"):
        return fake.text(max_nb_chars=80)
    if cn == "email":
        return fake.email()
    if cn == "phone":
        return fake.phone_number()
    if cn in ("city", "address"):
        return fake.city()
    if "price" in cn:
        return round(random.uniform(5, 5000), 2)
    if cn == "total":
        return round(random.uniform(10, 10000), 2)
    if "quantity" in cn:
        return random.randint(1, 10)
    if cn in ("stock", "count", "amount"):
        return random.randint(0, 200)
    if cn == "sku":
        return fake.bothify(text="??-######").upper()
    if cn == "status":
        return random.choice(["new", "processing", "delivered", "cancelled"])
    if cn == "rating":
        return random.randint(1, 5)
    if cn in ("created_at", "registered_at", "date"):
        return fake.date_between(start_date="-2y", end_date="today").isoformat()
    if cn == "unit_price":
        return round(random.uniform(5, 500), 2)
    # type fallbacks
    if "INT" in col_type.upper():
        return random.randint(1, 1000)
    if col_type.upper() in ("REAL", "FLOAT", "NUMERIC"):
        return round(random.uniform(1, 1000), 2)
    return fake.word()


def materialize_python(schema: dict, db_path: Path, n_rows: int) -> int:
    """Create DB + insert faker data entirely in Python (no go --materialize)."""
    db_path.unlink(missing_ok=True)
    db = sqlite3.connect(str(db_path))
    db.execute("PRAGMA foreign_keys = ON")

    ddl = build_ddl(schema)
    db.executescript(ddl)

    all_ids: dict[str, list] = {}  # table → list of PK values
    total = 0

    # Topological sort by FK dependencies (parent tables first)
    table_order = _topo_sort(schema)

    for tname in table_order:
        info = schema[tname]
        if not info["columns"]:
            continue
        col_names = [c[0] for c in info["columns"]]
        pk_cols = set(info["pks"])
        placeholders = ", ".join("?" for _ in col_names)
        stmt = f"INSERT INTO {tname} ({', '.join(col_names)}) VALUES ({placeholders})"

        for i in range(n_rows):
            row_vals = []
            for col, col_type, _ in info["columns"]:
                if col in pk_cols:
                    val = i + 1
                else:
                    fk = next((f for f in info["fks"] if f["from_col"] == col), None)
                    if fk and fk["to_table"] in all_ids and all_ids[fk["to_table"]]:
                        val = random.choice(all_ids[fk["to_table"]])
                    else:
                        val = _gen_value(col, col_type)
                row_vals.append(val)

            try:
                db.execute(stmt, row_vals)
            except sqlite3.IntegrityError:  # FK violation → skip
                continue

            if info["pks"]:
                all_ids.setdefault(tname, []).append(
                    row_vals[col_names.index(info["pks"][0])]
                )
            total += 1

    db.commit()
    db.close()
    return total


# ── config enrichment ──────────────────────────────────────────────────


def enrich_config(config: dict, schema: dict) -> dict:
    """Add custom_queries from FK relationships."""
    config.setdefault("custom_queries", {})
    for tname, info in schema.items():
        for fk in info["fks"]:
            pt, pc, cc = fk["to_table"], fk["to_col"], fk["from_col"]
            qid = f"{tname}_by_{pt}"
            if qid in config["custom_queries"]:
                continue
            config["custom_queries"][qid] = {
                "sql": f"SELECT t.* FROM {tname} t WHERE t.{cc} = ?",
                "params": ["id"],
                "result_mapping": {},
                "max_rows": 100,
            }
            ep = {
                "method": "GET",
                "path": f"/{pt}/{{id}}/{tname}",
                "op": "custom_query",
                "query_id": qid,
                "params": [
                    {"name": "id", "in": "path", "type": "string", "required": True}
                ],
                "description": f"Get {tname} for a specific {pt}",
            }
            if ep not in config.get("endpoints", []):
                config.setdefault("endpoints", []).append(ep)
    config.setdefault("data_source", {})["read_only"] = False
    return config


# ── main ───────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description="Foreign DB auto-gen pipeline")
    p.add_argument("--db", required=True, help="Path to foreign SQLite DB")
    p.add_argument("--name", default="shop", help="Scenario name")
    p.add_argument("--records", type=int, default=3, help="Rows per table")
    p.add_argument("--port", type=int, default=18084)
    p.add_argument("--skip-test", action="store_true")
    args = p.parse_args()

    src_db = Path(args.db).resolve()
    scenario = DATA_SERVICE / "testdata" / "scenarios" / args.name
    scenario.mkdir(parents=True, exist_ok=True)

    # Materialized DB alongside the scenario
    mat_db = scenario / "data.db"
    mat_db.unlink(missing_ok=True)

    # 1. Discover
    print("── 1. --discover ──")
    config = discover_config(src_db)
    entities = config.get("entities", [])
    print(f"   {len(entities)} entities: {[e['name'] for e in entities]}")

    # 2. Schema
    print("── 2. Schema ──")
    schema = get_schema(src_db)
    for t, info in schema.items():
        fk_s = (
            ", ".join(
                f"{f['from_col']}->{f['to_table']}.{f['to_col']}" for f in info["fks"]
            )
            or "none"
        )
        print(f"   {t}: {len(info['columns'])} cols, PK={info['pks']}, FK=[{fk_s}]")

    # 3. Python materialize (DDL + faker seed → new db)
    print(f"── 3. Materialize ({args.records} rows/table) ──")
    total = materialize_python(schema, mat_db, args.records)
    print(f"   {mat_db} ({mat_db.stat().st_size} bytes, {total} rows)")

    # 4. Enrich config
    print("── 4. Enrich config ──")
    config = enrich_config(config, schema)
    n_cq = len(config.get("custom_queries", {}))
    n_ep = len(config.get("endpoints", []))
    print(f"   custom_queries: {n_cq}, endpoints: {n_ep}")

    # 5. Update DSN to point to materialized DB
    config["data_source"]["dsn"] = str(mat_db)

    # 6. Write scenario
    print(f"── 5. Write scenario → {scenario} ──")
    (scenario / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"   config.json: {len(json.dumps(config))} bytes")

    if args.skip_test:
        print(f"\n✅ Scenario ready: {scenario}")
        print(f"   go run ./data-service/cmd/server/ --config {scenario}/config.json")
        return 0

    # 7. Start & test
    print(f"── 6. Start server & test (: {args.port}) ──")
    server = subprocess.Popen(
        [
            str(_GO),
            "run",
            "./data-service/cmd/server/",
            "--config",
            str(scenario / "config.json"),
        ],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PORT": str(args.port)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def cleanup():
        try:
            server.send_signal(signal.SIGTERM)
            server.wait(timeout=5)
        except Exception:
            pass

    atexit.register(cleanup)

    base = f"http://127.0.0.1:{args.port}"
    for i in range(20):
        try:
            with urlopen(
                Request(f"{base}/health", headers={"Accept": "application/json"}),
                timeout=2,
            ) as r:
                if json.loads(r.read()).get("status") == "ok":
                    print(f"   server ready ({i * 0.5:.1f}s)")
                    break
        except Exception:
            time.sleep(0.5)
    else:
        print("   FAIL: server didn't start")
        return 1

    failures = 0

    def check(label: str, path: str, fn=None):
        nonlocal failures
        url = f"{base}{path}"
        try:
            req = Request(url, headers={"Accept": "application/json"})
            with urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read())
                status = resp.status
        except Exception as exc:
            code = getattr(exc, "code", 0)
            try:
                body = json.loads(exc.read().decode()) if hasattr(exc, "read") else None
            except Exception:
                body = str(exc)
            status = code

        if fn:
            try:
                fn(status, body)
            except Exception as err:
                print(f"   ❌ {label}: {err}")
                failures += 1
                return
        elif status != 200:
            print(f"   ❌ {label}: {status}")
            failures += 1
            return
        print(f"   ✅ {label}")

    check(
        "health",
        "/health",
        lambda s, b: (_ for _ in ()).throw(AssertionError(f"{s}"))
        if s != 200 or b.get("status") != "ok"
        else None,
    )
    check(
        "openapi",
        "/openapi.json",
        lambda s, b: (_ for _ in ()).throw(AssertionError(f"{s}"))
        if s != 200 or b.get("openapi") != "3.1.0"
        else None,
    )
    check("stats", "/stats")

    # get_by_id for each entity
    for e in entities:
        name = e["name"]
        check(
            f"/{name}/1",
            f"/{name}/1",
            lambda s, b, n=name: (_ for _ in ()).throw(
                AssertionError(f"{n}: {s} {b.get('error', '')}")
            )
            if s != 200 or not isinstance(b, dict)
            else None,
        )

    # custom_queries (FK lookups) — empty list is OK (random may not hit id=1)
    for ep in config.get("endpoints", []):
        if ep.get("op") == "custom_query":
            path = ep["path"].replace("{id}", "1")
            check(
                path,
                path,
                lambda s, b, p=path: (_ for _ in ()).throw(AssertionError(f"{p}: {s}"))
                if s != 200
                else None,
            )

    print(f"\n{'=' * 40}")
    if failures == 0:
        print(
            f"✅ ALL PASSED  ({len(entities)} entities, {n_ep} endpoints, {total} rows)"
        )
    else:
        print(f"❌ {failures} FAILURES")
    print(f"{'=' * 40}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
