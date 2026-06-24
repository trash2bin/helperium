from faker import Faker
import json
import random

from fixtures.catalog import (
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

fake = Faker("ru_RU")


def ru_text(max_chars=200):
    result = ""

    while len(result) < max_chars:
        result += random.choice(TEXTS) + " "
    return result[:max_chars].strip()


def generate_groups():
    # Группы вынесены в отдельную сущность, как в реальных БД
    groups = []
    for name in GROUP_NAMES:
        prefix = name.split("-")[0]
        specialty = GROUP_SPECIALTY_MAP.get(prefix, random.choice(SPECIALITIES))
        groups.append({"id": fake.uuid4(), "name": name, "specialty": specialty})
    return groups


def generate_students(groups, num_students=40):
    students = []
    for _ in range(num_students):
        is_male = random.choice([True, False])
        student = {
            "id": fake.uuid4(),
            "name": fake.name_male() if is_male else fake.name_female(),
            "group_id": random.choice(groups)["id"],
            "course": random.randint(1, 4),
        }
        students.append(student)
    return students


def generate_teachers():
    teachers = []
    # Гарантируем, что у каждого предмета есть хотя бы один преподаватель
    assigned_disciplines = set()

    # Сначала раздаем обязательные предметы
    for disc in DISCIPLINE_NAMES:
        teacher = {"id": fake.uuid4(), "name": fake.name(), "disciplines": [disc]}
        teachers.append(teacher)
        assigned_disciplines.add(disc)

    # Теперь добавим еще учителей, которые ведут по 2-3 предмета
    for _ in range(5):
        extra_discs = random.sample(DISCIPLINE_NAMES, random.randint(1, 3))
        teacher = {"id": fake.uuid4(), "name": fake.name(), "disciplines": extra_discs}
        teachers.append(teacher)

    return teachers


def generate_disciplines():
    return [
        {"id": fake.uuid4(), "name": name, "description": ru_text(250)}
        for name in DISCIPLINE_NAMES
    ]


def generate_grades(students, groups, disciplines, num_grades=60):
    grades = []

    # Хелпер для быстрого поиска предметов группы
    group_disciplines = {}
    for group in groups:
        group_disciplines[group["id"]] = [
            d
            for d in disciplines
            if d["name"] in CURRICULUM.get(group["specialty"], [])
        ]

    for _ in range(num_grades):
        student = random.choice(students)
        # Берем предмет ТОЛЬКО из учебного плана группы студента!
        possible_discs = group_disciplines.get(student["group_id"], disciplines)
        if not possible_discs:
            continue

        discipline = random.choice(possible_discs)

        # Используем только балльную систему (или только слова, но не микс)
        grade_val = random.choices([5, 4, 3, 2], weights=[40, 35, 20, 5])[0]

        grades.append(
            {
                "id": fake.uuid4(),
                "student_id": student["id"],
                "discipline_id": discipline["id"],
                "grade": grade_val,
                "date": fake.date_between(start_date="-6m", end_date="today").strftime(
                    "%Y-%m-%d"
                ),
            }
        )
    return grades


def generate_schedule(groups, disciplines, teachers):
    schedule = []

    discipline_to_teachers = {}
    for disc in disciplines:
        possible_teachers = [t for t in teachers if disc["name"] in t["disciplines"]]
        if possible_teachers:
            discipline_to_teachers[disc["name"]] = possible_teachers

    for group in groups:
        # Определяем, что изучает эта группа
        group_specs = CURRICULUM.get(group["specialty"], [])
        group_disciplines = [d for d in disciplines if d["name"] in group_specs]

        for day in WEEK_DAYS:
            # В реальности пар обычно 2-4 в день
            daily_slots = random.sample(
                TIME_SLOTS,
                random.randint(2, min(4, len(TIME_SLOTS))),
            )
            daily_slots.sort()  # Сортируем по времени
            lessons = []

            for slot in daily_slots:
                if not group_disciplines:
                    break
                discipline = random.choice(group_disciplines)

                # Тип занятия
                lesson_type = random.choice(LESSON_TYPES)

                possible_teachers = discipline_to_teachers.get(
                    discipline["name"],
                    teachers[:1],
                )
                teacher = random.choice(possible_teachers)

                lessons.append(
                    {
                        "discipline_id": discipline["id"],
                        "discipline_name": discipline["name"],
                        "type": lesson_type,
                        "teacher_id": teacher["id"],
                        "teacher_name": teacher["name"],
                        # В реальности лекции чаще в 100-х, лабораторные в 300-х.
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
                        "group_name": group["name"],
                        "day": day,
                        "lessons": lessons,
                    }
                )
    return schedule


def generate_data():
    groups = generate_groups()
    students = generate_students(groups)
    teachers = generate_teachers()
    disciplines = generate_disciplines()

    schedule = generate_schedule(groups, disciplines, teachers)
    grades = generate_grades(students, groups, disciplines)

    data = {
        "groups": groups,
        "students": students,
        "teachers": teachers,
        "disciplines": disciplines,
        "materials": [],
        "schedule": schedule,
        "grades": grades,
    }

    with open("fixtures.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("Fixtures successfully generated!")


if __name__ == "__main__":
    generate_data()
