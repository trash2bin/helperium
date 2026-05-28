from faker import Faker
import json
import random

fake = Faker('ru_RU')

# Пул русских текстов
TEXTS = [
    "Данная дисциплина охватывает основные теоретические и практические аспекты.",
    "Курс предназначен для студентов, изучающих современные информационные технологии.",
    "В рамках дисциплины рассматриваются ключевые алгоритмы и методы решения задач.",
    "Материал включает лекции, лабораторные работы и самостоятельные задания.",
    "Освоение курса позволит применять полученные знания в профессиональной деятельности.",
]

SPECIALITIES = [
    'Информационные системы и технологии', 'Программная инженерия',
    'Информационная безопасность', 'Прикладная математика'
]

DISCIPLINE_NAMES = [
    'Алгоритмы и структуры данных', 'Базы данных', 'Веб-технологии',
    'Машинное обучение', 'Компьютерные сети', 'Операционные системы',
    'Компьютерная графика', 'Теория алгоритмов', 'Криптография', 'Искусственный интеллект'
]

# УЧЕБНЫЕ ПЛАНЫ: Какие специальности какие предметы изучают (Для реализма)
CURRICULUM = {
    'Информационные системы и технологии': ['Базы данных', 'Веб-технологии', 'Алгоритмы и структуры данных', 'Операционные системы'],
    'Программная инженерия': ['Алгоритмы и структуры данных', 'Веб-технологии', 'Базы данных', 'Теория алгоритмов'],
    'Информационная безопасность': ['Криптография', 'Компьютерные сети', 'Операционные системы', 'Алгоритмы и структуры данных'],
    'Прикладная математика': ['Машинное обучение', 'Искусственный интеллект', 'Теория алгоритмов', 'Алгоритмы и структуры данных'],
    'Вайбкодер': ['Операционные системы', 'Компьютерные сети', 'Искусственный интеллект', 'Машинное обучение', 'Компьютерная графика'],
}

def ru_text(max_chars=200):
    result = ""

    while len(result) < max_chars:
        result += random.choice(TEXTS) + " "
    return result[:max_chars].strip()

def generate_groups():
    # Группы вынесены в отдельную сущность, как в реальных БД
    groups = []
    names = ['ИВТ-21', 'ПИ-20', 'ИБ-22', 'ПМ-21', 'ИВТ-67', 'ПИ-14', 'ВБ-67', 'ВБ-144']
    group_specialty_map = {
        'ИВТ': 'Информационные системы и технологии',
        'ПИ': 'Программная инженерия',
        'ИБ': 'Информационная безопасность',
        'ПМ': 'Прикладная математика',
        'ВБ': 'Вайбкодер',
    }

    groups = []
    for name in names:
        prefix = name.split('-')[0]
        specialty = group_specialty_map.get(prefix, random.choice(SPECIALITIES))
        groups.append({
            "id": fake.uuid4(),
            "name": name,
            "specialty": specialty
        })
    return groups

def generate_students(groups, num_students=40):
    students = []
    for _ in range(num_students):
        is_male = random.choice([True, False])
        student = {
            "id": fake.uuid4(),
            "name": fake.name_male() if is_male else fake.name_female(),
            "group_id": random.choice(groups)['id'],
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
        teacher = {
            "id": fake.uuid4(),
            "name": fake.name(),
            "disciplines": [disc]
        }
        teachers.append(teacher)
        assigned_disciplines.add(disc)

    # Теперь добавим еще учителей, которые ведут по 2-3 предмета
    for _ in range(5):
        extra_discs = random.sample(DISCIPLINE_NAMES, random.randint(1, 3))
        teacher = {
            "id": fake.uuid4(),
            "name": fake.name(),
            "disciplines": extra_discs
        }
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
        group_disciplines[group['id']] = [
            d for d in disciplines if d['name'] in CURRICULUM.get(group['specialty'], [])
        ]

    for _ in range(num_grades):
        student = random.choice(students)
        # Берем предмет ТОЛЬКО из учебного плана группы студента!
        possible_discs = group_disciplines.get(student['group_id'], disciplines)
        if not possible_discs: continue

        discipline = random.choice(possible_discs)

        # Используем только балльную систему (или только слова, но не микс)
        grade_val = random.choices([5, 4, 3, 2], weights=[40, 35, 20, 5])[0]

        grades.append({
            "id": fake.uuid4(),
            "student_id": student['id'],
            "discipline_id": discipline['id'],
            "grade": grade_val,
            "date": fake.date_between(start_date='-6m', end_date='today').strftime('%Y-%m-%d')
        })
    return grades

def generate_schedule(groups, disciplines, teachers):
    schedule = []
    days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница']
    time_slots = ["08:00-9:35 (1 пара)", "9:45-11:20 (2 пара)", "11:33-13:00 (3 пара)", "13:15-15:00 (4 пара)"]

    discipline_to_teachers = {}
    for disc in disciplines:
        possible_teachers = [t for t in teachers if disc['name'] in t['disciplines']]
        if possible_teachers:
            discipline_to_teachers[disc['name']] = possible_teachers

    for group in groups:
        # Определяем, что изучает эта группа
        group_specs = CURRICULUM.get(group['specialty'], [])
        group_disciplines = [d for d in disciplines if d['name'] in group_specs]

        for day in days:
            # В реальности пар обычно 2-4 в день
            daily_slots = random.sample(time_slots, random.randint(2, min(4, len(time_slots))))
            daily_slots.sort() # Сортируем по времени
            lessons = []

            for slot in daily_slots:
                if not group_disciplines: break
                discipline = random.choice(group_disciplines)

                # Тип занятия
                lesson_type = random.choice(['Лекция', 'Практика'])

                possible_teachers = discipline_to_teachers.get(discipline['name'], teachers[:1])
                teacher = random.choice(possible_teachers)

                lessons.append({
                    "discipline_id": discipline['id'],
                    "discipline_name": discipline['name'],
                    "type": lesson_type,
                    "teacher_id": teacher['id'],
                    "teacher_name": teacher['name'],
                    "room": random.randint(100, 500), # В реальности лекции в 100-х, лабы в 300-х
                    "time_slot": slot,
                    "week_type": random.choice(['Числитель', 'Знаменатель', 'Обе'])
                })

            if lessons:
                schedule.append({
                    "id": fake.uuid4(),
                    "group_id": group['id'],
                    "group_name": group['name'],
                    "day": day,
                    "lessons": lessons
                })
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
        "grades": grades
    }

    with open('fixtures.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("Fixtures successfully generated!")

if __name__ == "__main__":
    generate_data()
