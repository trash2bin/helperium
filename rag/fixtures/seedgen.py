"""Генератор seed-данных для university DB.

Создаёт seed.json — JSON с группами, студентами, преподавателями,
дисциплинами, расписанием и оценками. Файл читается Go-сервисом data-service
при запуске с флагом --seed.

Раньше жил в fixtures/. Переехал сюда вместе с остальными CLI rag
(потому что генерация seed-данных — это dev-only утилита, тесно связанная
с сервисами, а fixtures/ как workspace member упразднён).

Использование:
    uv run agent-seedgen                                 # дефолт: 40 студентов
    uv run agent-seedgen --students 80 --grades 200       # кастомный размер
    uv run agent-seedgen --out /tmp/my-seed.json          # другой путь
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from faker import Faker

from helperium_sdk.seed_models import StorageSeed
from rag.fixtures.catalog import (
    CURRICULUM,
    DISCIPLINE_NAMES,
    GROUP_NAMES,
    GROUP_SPECIALTY_MAP,
    LESSON_TYPES,
    SPECIALITIES,
    TEXTS,
    TIME_SLOTS,
    WEEK_DAYS,
    WEEK_TYPES,
)

# specs/fixtures/seed.json — рядом с OpenAPI-схемами, как и другие
# технические артефакты проекта. gitignore'd: регенерируется через
# `uv run agent-seedgen` (детерминирован при --seed 42).
SEED_PATH = Path(__file__).resolve().parents[2] / "specs" / "fixtures" / "seed.json"
SEED_PATH.parent.mkdir(parents=True, exist_ok=True)


def ru_text(max_chars: int = 200) -> str:
    result = ""
    while len(result) < max_chars:
        result += random.choice(TEXTS) + " "
    return result[:max_chars].strip()


def generate_groups() -> list[dict]:
    groups = []
    for name in GROUP_NAMES:
        prefix = name.split("-")[0]
        specialty = GROUP_SPECIALTY_MAP.get(prefix, random.choice(SPECIALITIES))
        groups.append(
            {
                "id": fake.uuid4(),
                "name": name,
                "speciality": specialty,
            }
        )
    return groups


def generate_students(groups: list[dict], num_students: int) -> list[dict]:
    students = []
    for _ in range(num_students):
        is_male = random.choice([True, False])
        students.append(
            {
                "id": fake.uuid4(),
                "name": fake.name_male() if is_male else fake.name_female(),
                "group_id": random.choice(groups)["id"],
                "course": random.randint(1, 4),
            }
        )
    return students


def generate_teachers() -> list[dict]:
    """Каждой дисциплине — минимум один преподаватель. Плюс ещё 5 универсальных."""
    teachers = []
    # Гарантируем покрытие каждой дисциплины
    for disc in DISCIPLINE_NAMES:
        teachers.append(
            {
                "id": fake.uuid4(),
                "name": fake.name(),
                "disciplines": [disc],
            }
        )
    # Дополнительные преподаватели, ведущие 1-3 дисциплины
    for _ in range(5):
        teachers.append(
            {
                "id": fake.uuid4(),
                "name": fake.name(),
                "disciplines": random.sample(DISCIPLINE_NAMES, random.randint(1, 3)),
            }
        )
    return teachers


def generate_disciplines() -> list[dict]:
    return [
        {
            "id": fake.uuid4(),
            "name": name,
            "description": ru_text(250),
        }
        for name in DISCIPLINE_NAMES
    ]


def generate_schedule(
    groups: list[dict],
    disciplines: list[dict],
    teachers: list[dict],
) -> list[dict]:
    discipline_to_teachers: dict[str, list[dict]] = {}
    for disc in disciplines:
        possible = [t for t in teachers if disc["name"] in t["disciplines"]]
        if possible:
            discipline_to_teachers[disc["name"]] = possible

    schedule = []
    for group in groups:
        group_specs = CURRICULUM.get(group["speciality"], [])
        group_disciplines = [d for d in disciplines if d["name"] in group_specs]
        if not group_disciplines:
            continue

        for day in WEEK_DAYS:
            daily_slots = sorted(
                random.sample(TIME_SLOTS, random.randint(2, min(4, len(TIME_SLOTS))))
            )
            lessons = []
            for slot in daily_slots:
                discipline = random.choice(group_disciplines)
                lesson_type = random.choice(LESSON_TYPES)
                possible_teachers = discipline_to_teachers.get(discipline["name"], [])
                if not possible_teachers:
                    continue
                teacher = random.choice(possible_teachers)
                lessons.append(
                    {
                        "discipline_id": discipline["id"],
                        "discipline_name": discipline["name"],
                        "teacher_name": teacher["name"],
                        "type": lesson_type,
                        "room": random.randint(100, 500),
                        "time_slot": slot,
                        "week_type": random.choice(WEEK_TYPES),
                    }
                )
            if lessons:
                schedule.append(
                    {
                        "id": fake.uuid4(),
                        "group_id": group["id"],
                        "day": day,
                        "lessons": lessons,
                    }
                )
    return schedule


def generate_grades(
    students: list[dict],
    groups: list[dict],
    disciplines: list[dict],
    num_grades: int,
) -> list[dict]:
    # Кэш: дисциплины по специальности группы
    group_disciplines: dict[str, list[dict]] = {}
    for group in groups:
        group_disciplines[group["id"]] = [
            d
            for d in disciplines
            if d["name"] in CURRICULUM.get(group["speciality"], [])
        ]

    grades = []
    for _ in range(num_grades):
        student = random.choice(students)
        possible_discs = group_disciplines.get(student["group_id"], disciplines)
        if not possible_discs:
            continue
        discipline = random.choice(possible_discs)
        # Реалистичное распределение оценок
        grade_val = random.choices([5, 4, 3, 2], weights=[40, 35, 20, 5])[0]
        grades.append(
            {
                "id": fake.uuid4(),
                "student_id": student["id"],
                "discipline_id": discipline["id"],
                "grade": str(grade_val),
                "date": fake.date_between(start_date="-6m", end_date="today").strftime(
                    "%Y-%m-%d"
                ),
            }
        )
    return grades


def generate(
    num_students: int = 40,
    num_grades: int = 60,
    seed_value: int | None = 42,
) -> dict:
    """Сгенерировать полный набор seed-данных. Возвращает dict."""
    global fake
    fake = Faker("ru_RU")
    if seed_value is not None:
        Faker.seed(seed_value)
        random.seed(seed_value)

    groups = generate_groups()
    students = generate_students(groups, num_students)
    teachers = generate_teachers()
    disciplines = generate_disciplines()
    schedule = generate_schedule(groups, disciplines, teachers)
    grades = generate_grades(students, groups, disciplines, num_grades)

    return {
        "groups": groups,
        "students": students,
        "teachers": teachers,
        "disciplines": disciplines,
        "schedule": schedule,
        "grades": grades,
    }


def validate_seed(data: dict) -> None:
    """Валидировать сгенерированный seed против StorageSeed (Pydantic).

    Бросает pydantic.ValidationError если структура расходится с моделью.
    Если расходится — это drift между Go seedgen и Python Pydantic seed_models.
    """
    StorageSeed.model_validate(data)


# Инициализация глобального fake (перезаписывается в generate())
fake = Faker("ru_RU")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Генератор seed-данных для university DB"
    )
    parser.add_argument("--students", type=int, default=40, help="Количество студентов")
    parser.add_argument("--grades", type=int, default=60, help="Количество оценок")
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        dest="seed_value",
        help="Random seed для воспроизводимости",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SEED_PATH,
        help="Путь для сохранения seed JSON",
    )
    args = parser.parse_args()

    data = generate(
        num_students=args.students,
        num_grades=args.grades,
        seed_value=args.seed_value,
    )

    # Валидация через Pydantic перед записью — ловит drift структуры
    validate_seed(data)

    args.out.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Seed written to {args.out}")
    print(f"  groups:      {len(data['groups'])}")
    print(f"  students:    {len(data['students'])}")
    print(f"  teachers:    {len(data['teachers'])}")
    print(f"  disciplines: {len(data['disciplines'])}")
    print(f"  schedule:    {len(data['schedule'])}")
    print(f"  grades:      {len(data['grades'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
