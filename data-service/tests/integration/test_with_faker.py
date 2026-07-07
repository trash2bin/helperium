"""
Integration test: faker seed -> PostgreSQL (Docker) -> data-service -> curl.

Generates a random seed.json via faker, creates a scenario,
materializes into PostgreSQL (docker compose db) and checks all endpoints.

Usage:
    cd agent-tutor
    docker compose up -d db                     # once
    uv run python data-service/tests/integration/test_with_faker.py
    uv run python data-service/tests/integration/test_with_faker.py --seed 42 --students 20 --grades 60
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote as urlencode
from urllib.request import Request, urlopen

from faker import Faker

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_SERVICE = PROJECT_ROOT / "data-service"

PG_DSN = "postgresql://tutor:tutor@127.0.0.1:5432/agent_tutor?sslmode=disable"

fake = Faker("ru_RU")


# ---------------------------------------------------------------------------
# Faker seed generator (аналог agent-seedgen, но на коленке)
# ---------------------------------------------------------------------------

DISCIPLINES = [
    ("d1", "Базы данных", "Реляционные и нереляционные БД"),
    ("d2", "Алгоритмы", "Сортировки, графы, деревья"),
    ("d3", "Машинное обучение", "Классификация, регрессия, кластеризация"),
    ("d4", "Операционные системы", "Процессы, память, файловые системы"),
    ("d5", "Компьютерные сети", "TCP/IP, HTTP, DNS, маршрутизация"),
    ("d6", "Веб-технологии", "HTML, CSS, JavaScript, React"),
    ("d7", "Python", "Основы языка, ООП, async"),
    ("d8", "Go", "Конкурентность, горутины, каналы"),
    ("d9", "Безопасность", "Криптография, аутентификация, XSS/SQLi"),
    ("d10", "Тестирование", "Unit, integration, E2E"),
]
DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота"]
LESSON_TYPES = ["лекция", "практика", "лабораторная"]
TIME_SLOTS = ["08:30-10:05", "10:15-11:50", "12:10-13:45", "14:00-15:35", "15:45-17:20"]


def generate_seed(
    n_groups: int = 3,
    n_students: int = 10,
    n_teachers: int = 4,
    n_disciplines: int = 6,
    n_grades: int = 5,
    n_schedule_slots: int = 3,
    seed: int | None = 42,
) -> dict:
    rng = random.Random(seed)
    Faker.seed(seed or 0)
    fake.seed_instance(seed or 0)

    group_ids = [f"g{i + 1}" for i in range(n_groups)]
    groups = [
        {
            "id": gid,
            "name": f"Группа {fake.bothify(text='??-###')}",
            "speciality": rng.choice(
                [
                    "Программная инженерия",
                    "Информатика",
                    "Кибербезопасность",
                    "Data Science",
                ]
            ),
        }
        for gid in group_ids
    ]

    disc_subset = DISCIPLINES[:n_disciplines]
    disciplines = [
        {"id": did, "name": name, "description": desc}
        for did, name, desc in disc_subset
    ]

    teacher_ids = [f"t{i + 1}" for i in range(n_teachers)]
    teachers = []
    for tid in teacher_ids:
        n = rng.randint(1, min(3, n_disciplines))
        teacher_disciplines = [d["id"] for d in rng.sample(disciplines, n)]
        teachers.append(
            {
                "id": tid,
                "name": f"{fake.last_name()} {fake.first_name_female()} {fake.middle_name_female()}",
                "disciplines": teacher_disciplines,
            }
        )

    student_ids = [f"s{i + 1}" for i in range(n_students)]
    students = [
        {
            "id": sid,
            "name": f"{fake.last_name()} {fake.first_name()} {fake.middle_name()}",
            "group_id": rng.choice(group_ids),
            "course": rng.choice([1, 2, 3, 4]),
        }
        for sid in student_ids
    ]

    schedule_ids = [f"sch{i + 1}" for i in range(n_schedule_slots * n_groups)]
    schedule = []
    for gid in group_ids:
        for slot in range(n_schedule_slots):
            sch_id = schedule_ids.pop(0)
            day = DAYS[slot % len(DAYS)]
            n_lessons = rng.randint(2, 4)
            lessons = []
            for li in range(n_lessons):
                disc = rng.choice(disciplines)
                teacher = rng.choice(teachers)
                lessons.append(
                    {
                        "discipline_id": disc["id"],
                        "discipline_name": disc["name"],
                        "teacher_name": teacher["name"],
                        "type": rng.choice(LESSON_TYPES),
                        "room": rng.randint(100, 500),
                        "time_slot": TIME_SLOTS[li % len(TIME_SLOTS)],
                        "week_type": rng.choice(["числитель", "знаменатель", "обе"]),
                    }
                )
            schedule.append(
                {
                    "id": sch_id,
                    "group_id": gid,
                    "day": day,
                    "lessons": lessons,
                }
            )

    grades = []
    for s in students:
        student_disciplines = rng.sample(disciplines, min(n_grades, n_disciplines))
        for gi, disc in enumerate(student_disciplines):
            grades.append(
                {
                    "id": f"gr_{s['id']}_{gi + 1}",
                    "student_id": s["id"],
                    "discipline_id": disc["id"],
                    "grade": str(rng.choice([3, 4, 5])),
                    "date": fake.date_between(
                        start_date="-2y", end_date="today"
                    ).isoformat(),
                }
            )

    return {
        "groups": groups,
        "students": students,
        "teachers": teachers,
        "disciplines": disciplines,
        "schedule": schedule,
        "grades": grades,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"  RUN: {' '.join(cmd)}")
    return subprocess.run(
        cmd, check=True, capture_output=True, text=True, cwd=str(PROJECT_ROOT), **kwargs
    )


def http_get(url: str) -> "tuple[int, dict | list | str]":
    req = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            if "application/json" in resp.headers.get("Content-Type", ""):
                return resp.status, json.loads(body)
            return resp.status, body
    except Exception as e:
        # HTTPError carries status code in .code
        if hasattr(e, "code"):
            err_body = e.read().decode() if hasattr(e, "read") else ""
            try:
                return e.code, json.loads(err_body)
            except Exception:
                return e.code, err_body
        raise


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Faker integration test")
    parser.add_argument("--students", type=int, default=10)
    parser.add_argument("--groups", type=int, default=3)
    parser.add_argument("--teachers", type=int, default=4)
    parser.add_argument("--disciplines", type=int, default=6)
    parser.add_argument("--grades", type=int, default=5)
    parser.add_argument("--schedule", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--port", type=int, default=18084, help="data-service port (avoid clash)"
    )
    parser.add_argument(
        "--out",
        type=str,
        default=None,
        help="save seed.json to this path and exit (skip tests)",
    )
    args = parser.parse_args()

    # -- 1. Check PG is up --
    print("-- 1. Checking PostgreSQL --")
    pg_check = subprocess.run(
        ["docker", "exec", "agent-tutor-db-1", "pg_isready", "-U", "tutor"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if pg_check.returncode != 0:
        print("FAIL: PostgreSQL not running. Run: docker compose up -d db")
        return 1
    print("   OK: PG is ready")

    # -- 2. Generate random seed --
    print(f"-- 2. Generating seed (seed={args.seed}, students={args.students}) --")
    seed = generate_seed(
        n_groups=args.groups,
        n_students=args.students,
        n_teachers=args.teachers,
        n_disciplines=args.disciplines,
        n_grades=args.grades,
        n_schedule_slots=args.schedule,
        seed=args.seed,
    )
    total = sum(
        len(seed.get(k, []))
        for k in ["groups", "students", "teachers", "disciplines", "schedule", "grades"]
    )
    print(
        f"   {len(seed['groups'])} groups, {len(seed['students'])} students, "
        f"{len(seed['teachers'])} teachers, {len(seed['disciplines'])} disciplines, "
        f"{len(seed['schedule'])} schedule entries, {len(seed['grades'])} grades"
    )
    print(f"   total entities: {total}")

    # -- 2b. If --out requested, write seed.json and exit --
    if args.out:
        out_path = Path(args.out)
        out_path.write_text(
            json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"   saved seed.json -> {out_path} ({total} entities)")
        return 0

    # -- 3. Create scenario dir --
    print("-- 3. Creating scenario --")
    scenario_dir = Path(tempfile.mkdtemp(prefix="faker-scenario-"))
    print(f"   dir: {scenario_dir}")

    (scenario_dir / "seed.json").write_text(
        json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Copy PG config from postgres-testseed and tweak DSN
    pg_config_src = (
        DATA_SERVICE / "testdata" / "scenarios" / "postgres-testseed" / "config.json"
    )
    config = json.loads(pg_config_src.read_text(encoding="utf-8"))
    config["data_source"]["dsn"] = PG_DSN
    config["data_source"]["read_only"] = False
    (scenario_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # -- 4. Drop & recreate PG schema --
    print("-- 4. Cleaning PG schema --")
    subprocess.run(
        [
            "docker",
            "exec",
            "agent-tutor-db-1",
            "psql",
            "-U",
            "tutor",
            "-d",
            "agent_tutor",
            "-c",
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    print("   OK: schema reset")

    # -- 5. Materialize --
    print("-- 5. Materializing --")
    result = run(
        [
            "go",
            "run",
            "./data-service/cmd/server/",
            "--materialize",
            str(scenario_dir),
        ]
    )
    last_line = result.stdout.strip().split("\n")[-1]
    print(f"   {last_line}")

    # -- 6. Start server --
    print(f"-- 6. Starting server on :{args.port} --")
    server = subprocess.Popen(
        [
            "go",
            "run",
            "./data-service/cmd/server/",
            "--config",
            str(scenario_dir / "config.json"),
        ],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "PORT": str(args.port)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    def cleanup():
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=5)

    import atexit

    atexit.register(cleanup)

    base_url = f"http://127.0.0.1:{args.port}"
    for i in range(20):
        try:
            status, body = http_get(f"{base_url}/health")
            if status == 200 and body.get("status") == "ok":
                print(f"   OK: server ready after {i * 0.5:.1f}s")
                break
        except Exception:
            time.sleep(0.5)
    else:
        print("FAIL: Server didn't start")
        return 1

    # -- 7. Test endpoints --
    print("-- 7. Testing endpoints --")
    failures = 0

    def check(label: str, url_path: str, *assertions):
        nonlocal failures
        url = f"{base_url}{url_path}"
        try:
            status, body = http_get(url)
        except Exception as e:
            print(f"   FAIL {label}: HTTP error: {e}")
            failures += 1
            return
        if not assertions:
            if status == 200:
                print(f"   OK {label}: 200")
            else:
                print(f"   FAIL {label}: expected 200, got {status}")
                failures += 1
            return
        for fn in assertions:
            try:
                fn(status, body)
            except AssertionError as e:
                print(f"   FAIL {label}: {e}")
                failures += 1
                return
        print(f"   OK {label}")

    # --- entity-independent checks (just 200) ---
    check(
        "health",
        "/health",
        lambda s, b: (s == 200 and b.get("status") == "ok")
        or (_ for _ in ()).throw(AssertionError(f"bad health: {s} {b}")),
    )
    check(
        "stats",
        "/stats",
        lambda s, b: (
            s == 200 and isinstance(b.get("students"), int) and b["students"] > 0
        )
        or (_ for _ in ()).throw(AssertionError(f"bad stats: {s} {b}")),
    )
    check(
        "disciplines",
        "/disciplines",
        lambda s, b: (s == 200 and isinstance(b, list) and len(b) == args.disciplines)
        or (_ for _ in ()).throw(
            AssertionError(
                f"bad disciplines: {s}, len={len(b) if isinstance(b, list) else '?'}"
            )
        ),
    )
    check(
        "teachers find",
        f"/teachers?name={urlencode(seed['teachers'][0]['name'])}",
        lambda s, b: (s == 200 and b.get("full_name") == seed["teachers"][0]["name"])
        or (_ for _ in ()).throw(AssertionError(f"bad teacher: {s} {b}")),
    )
    check(
        "openapi.json",
        "/openapi.json",
        lambda s, b: (s == 200 and b.get("openapi") == "3.1.0")
        or (_ for _ in ()).throw(AssertionError("bad openapi")),
    )
    check(
        "swagger ui",
        "/docs",
        lambda s, b: (s == 200 and "html" in str(b)[:100].lower())
        or (_ for _ in ()).throw(AssertionError(f"bad docs: {s}")),
    )

    # --- per-entity checks ---
    s1 = seed["students"][0]
    check(
        f"student {s1['id']}",
        f"/students/{s1['id']}",
        lambda s, b: (s == 200 and b.get("full_name") == s1["name"])
        or (_ for _ in ()).throw(AssertionError(f"student mismatch: {b}")),
    )

    check(
        f"grades {s1['id']}",
        f"/students/{s1['id']}/grades",
        lambda s, b: (s == 200 and isinstance(b, list) and len(b) > 0)
        or (_ for _ in ()).throw(AssertionError(f"no grades for {s1['id']}")),
    )

    # --- not found ---
    check(
        "404 student",
        "/students/nonexistent",
        lambda s, b: (s == 404)
        or (_ for _ in ()).throw(AssertionError(f"expected 404: {s} {b}")),
    )

    # --- all-entities list endpoints ---
    check(
        "all grades",
        "/grades",
        lambda s, b: (
            s == 200 and len(b) > 0 and len(b) <= 80
        )  # 80 = max_rows from config
        or (_ for _ in ()).throw(
            AssertionError(
                f"grades bad: got {len(b) if isinstance(b, list) else '?'}, expected 1-80"
            )
        ),
    )
    check(
        "all schedule",
        "/schedule",
        lambda s, b: (s == 200 and len(b) == len(seed["schedule"]))
        or (_ for _ in ()).throw(
            AssertionError(
                f"schedule count mismatch: got {len(b)}, expected {len(seed['schedule'])}"
            )
        ),
    )
    check(
        "all students",
        "/students",
        lambda s, b: (s == 200 and len(b) == len(seed["students"]))
        or (_ for _ in ()).throw(
            AssertionError(
                f"students count mismatch: got {len(b)}, expected {len(seed['students'])}"
            )
        ),
    )

    # -- 8. Report --
    print(f"\n{'=' * 50}")
    if failures == 0:
        print(
            f"ALL PASSED ({total} entities, {len(seed['students'])} students, {len(seed['grades'])} grades)"
        )
    else:
        print(f"{failures} FAILURE(S)")
    print(f"{'=' * 50}")

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
