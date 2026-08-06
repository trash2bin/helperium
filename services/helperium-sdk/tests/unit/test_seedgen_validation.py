"""Тесты: seed.json (генерируется Python) валиден по структуре и FK-инвариантам.

Архитектурное решение (см. AGENTS.md):
- Go data-service = source of truth для структуры БД
- rag/fixtures/seedgen.py = генерирует seed.json, использует faker

Тесты в этом файле проверяют что seed.json имеет корректную структуру,
уникальные id и FK-целостность между коллекциями.

Запуск:
    uv run pytest helperium-sdk/tests/unit/test_seedgen_validation.py -v
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_PATH = REPO_ROOT / "specs" / "fixtures" / "seed.json"


def _load_seed() -> dict:
    """Загрузить текущий seed.json из репо."""
    if not SEED_PATH.exists():
        pytest.skip(f"seed.json not found at {SEED_PATH} — run `uv run agent-seedgen`")
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


# === Тест: seed.json существует ===


def test_seed_json_exists():
    """specs/fixtures/seed.json должен существовать (генерируется agent-seedgen)."""
    if not SEED_PATH.exists():
        pytest.skip(f"Run `uv run agent-seedgen` to generate {SEED_PATH}")


# === Тесты: FK-целостность (реальные инварианты, не зависят от схем) ===


def test_seed_fk_consistency_students_to_groups():
    """Каждый student.group_id указывает на существующий group.id."""
    seed = _load_seed()
    if "students" not in seed or "groups" not in seed:
        pytest.skip("seed.json missing students/groups")

    group_ids = {g["id"] for g in seed["groups"]}
    orphan = [s["id"] for s in seed["students"] if s.get("group_id") not in group_ids]

    assert not orphan, (
        f"{len(orphan)}/{len(seed['students'])} students reference non-existent groups.\n"
        f"Examples: {orphan[:3]}"
    )


def test_seed_fk_consistency_schedule_to_groups():
    """Каждый schedule.group_id указывает на существующий group.id."""
    seed = _load_seed()
    if "schedule" not in seed or "groups" not in seed:
        pytest.skip("seed.json missing schedule/groups")

    group_ids = {g["id"] for g in seed["groups"]}
    orphan = [s["id"] for s in seed["schedule"] if s.get("group_id") not in group_ids]

    assert not orphan, (
        f"{len(orphan)}/{len(seed['schedule'])} schedule entries reference non-existent groups.\n"
        f"Examples: {orphan[:3]}"
    )


def test_seed_fk_consistency_grades_to_students():
    """Каждый grade.student_id указывает на существующего student.id."""
    seed = _load_seed()
    if "grades" not in seed or "students" not in seed:
        pytest.skip("seed.json missing grades/students")

    student_ids = {s["id"] for s in seed["students"]}
    orphan = [g["id"] for g in seed["grades"] if g.get("student_id") not in student_ids]

    assert not orphan, (
        f"{len(orphan)}/{len(seed['grades'])} grades reference non-existent students.\n"
        f"Examples: {orphan[:3]}"
    )


def test_seed_fk_consistency_grades_to_disciplines():
    """Каждый grade.discipline_id указывает на существующую discipline.id."""
    seed = _load_seed()
    if "grades" not in seed or "disciplines" not in seed:
        pytest.skip("seed.json missing grades/disciplines")

    discipline_ids = {d["id"] for d in seed["disciplines"]}
    orphan = [
        g["id"] for g in seed["grades"] if g.get("discipline_id") not in discipline_ids
    ]

    assert not orphan, (
        f"{len(orphan)}/{len(seed['grades'])} grades reference non-existent disciplines.\n"
        f"Examples: {orphan[:3]}"
    )


def test_seed_fk_consistency_schedule_lessons():
    """Каждый lesson.discipline_id в schedule указывает на существующую discipline."""
    seed = _load_seed()
    if "schedule" not in seed or "disciplines" not in seed:
        pytest.skip("seed.json missing schedule/disciplines")

    discipline_ids = {d["id"] for d in seed["disciplines"]}
    orphan_lessons = []
    for entry in seed["schedule"]:
        for lesson in entry.get("lessons", []):
            if lesson.get("discipline_id") not in discipline_ids:
                orphan_lessons.append((entry["id"], lesson))

    assert not orphan_lessons, (
        f"{len(orphan_lessons)} lessons reference non-existent disciplines.\n"
        f"Examples: {orphan_lessons[:3]}"
    )


# === Тест: уникальность UUID ===


@pytest.mark.parametrize(
    "collection",
    [
        "groups",
        "students",
        "teachers",
        "disciplines",
        "schedule",
        "grades",
    ],
)
def test_seed_ids_are_unique(collection):
    """Все id в коллекции уникальны."""
    seed = _load_seed()
    if collection not in seed:
        pytest.skip(f"seed.json has no {collection!r}")

    ids = [item.get("id") for item in seed[collection] if "id" in item]
    duplicates = list({i for i in ids if ids.count(i) > 1})

    assert not duplicates, f"seed.json[{collection}] has duplicate id: {duplicates[:5]}"


# === Главный тест: структура seed.json ===
#
# seed.json пишется в STORAGE-формате (group_id FK, name строкой).
# Тест ниже проверяет топ-уровневую структуру, количество записей
# и FK-целостность — без Pydantic-моделей.


def test_seedgen_dry_run_produces_valid_structure():
    """agent-seedgen в --out режиме выдаёт корректную структуру.

    Проверяет:
    1. Все обязательные top-level коллекции есть.
    2. FK-инварианты выполняются.
    3. Количество сгенерированных записей соответствует CLI-аргументам.
    """
    out_path = Path("/tmp/test_seed_validation.json")
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "rag.fixtures.seedgen",
                "--students",
                "5",
                "--grades",
                "10",
                "--seed",
                "42",
                "--out",
                str(out_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        pytest.skip("Cannot run agent-seedgen (Python module not found)")

    if result.returncode != 0:
        pytest.fail(
            f"agent-seedgen failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    if not out_path.exists():
        pytest.fail(f"agent-seedgen didn't write to {out_path}")

    generated = json.loads(out_path.read_text(encoding="utf-8"))

    # Top-level structure
    for key in ("groups", "students", "teachers", "disciplines", "schedule", "grades"):
        assert key in generated, f"seed.json missing top-level key {key!r}"

    # Counts match CLI args
    assert len(generated["students"]) == 5
    assert len(generated["grades"]) == 10
    assert len(generated["groups"]) > 0
    assert len(generated["disciplines"]) > 0
    assert len(generated["teachers"]) > 0

    # FK consistency
    group_ids = {g["id"] for g in generated["groups"]}
    student_ids = {s["id"] for s in generated["students"]}
    discipline_ids = {d["id"] for d in generated["disciplines"]}

    for s in generated["students"]:
        assert s["group_id"] in group_ids, f"student {s['id']!r} → orphan group_id"
    for entry in generated["schedule"]:
        assert entry["group_id"] in group_ids, (
            f"schedule {entry['id']!r} → orphan group_id"
        )
        for lesson in entry.get("lessons", []):
            assert lesson["discipline_id"] in discipline_ids, (
                f"lesson in schedule {entry['id']!r} → orphan discipline_id"
            )
    for g in generated["grades"]:
        assert g["student_id"] in student_ids, f"grade {g['id']!r} → orphan student_id"
        assert g["discipline_id"] in discipline_ids, (
            f"grade {g['id']!r} → orphan discipline_id"
        )
