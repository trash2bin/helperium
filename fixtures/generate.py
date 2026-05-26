from faker import Faker
import json
import random

fake = Faker('ru_RU')

# Пул русских текстов для описаний и контента
TEXTS = [
    "Данная дисциплина охватывает основные теоретические и практические аспекты.",
    "Курс предназначен для студентов, изучающих современные информационные технологии.",
    "В рамках дисциплины рассматриваются ключевые алгоритмы и методы решения задач.",
    "Материал включает лекции, лабораторные работы и самостоятельные задания.",
    "Освоение курса позволит применять полученные знания в профессиональной деятельности.",
]

SPECIALITIES = [
    'Информационные системы и технологии',
    'Программная инженерия',
    'Информационная безопасность',
    'Прикладная математика',
    'Киберфизические системы'
]

DISCIPLINE_NAMES = [
    'Алгоритмы и структуры данных', 'Базы данных', 'Веб-технологии',
    'Машинное обучение', 'Компьютерные сети', 'Операционные системы',
    'Компьютерная графика', 'Теория алгоритмов', 'Криптография', 'Искусственный интеллект'
]

def ru_text(max_chars=200):
    result = ""
    while len(result) < max_chars // 2:
        result += random.choice(TEXTS) + " "
    return result[:max_chars].strip()

def generate_students(num_students=10):
    students = []
    groups = ['ИВТ-21', 'ПИ-20', 'ИБ-22', 'ПМ-21', 'ИС-20']
    for _ in range(num_students):
        is_male = random.choice([True, False])
        student = {
            "id": fake.uuid4(),
            "name": fake.name_male() if is_male else fake.name_female(),
            "group": random.choice(groups),
            "course": random.randint(1, 4),
            "specialty": random.choice(SPECIALITIES)
        }
        students.append(student)
    return students

def generate_teachers(num_teachers=5):
    teachers = []
    for _ in range(num_teachers):
        is_male = random.choice([True, False])
        # Учитель ведет от 1 до 3 дисциплин, чтобы было реалистичнее
        num_disciplines = random.randint(1, 3)
        teacher_disciplines = random.sample(DISCIPLINE_NAMES, min(num_disciplines, len(DISCIPLINE_NAMES)))

        teacher = {
            "id": fake.uuid4(),
            "name": fake.name_male() if is_male else fake.name_female(),
            "disciplines": teacher_disciplines
        }
        teachers.append(teacher)
    return teachers

def generate_disciplines():
    disciplines = []
    # Создаем уникальные дисциплины из списка
    used_names = set()
    for name in DISCIPLINE_NAMES:
        if name not in used_names:
            discipline = {
                "id": fake.uuid4(),
                "name": name,
                "description": ru_text(200)
            }
            disciplines.append(discipline)
            used_names.add(name)
    return disciplines

def generate_materials(disciplines, num_materials=20):
    materials = []
    for _ in range(num_materials):
        disc = random.choice(disciplines)
        material = {
            "id": fake.uuid4(),
            "discipline_id": disc['id'],
            "type": random.choice(['Лекция', 'Методичка', 'Задание']),
            "content": ru_text(500)
        }
        materials.append(material)
    return materials

def generate_grades(students, disciplines, num_grades=50):
    grades = []
    for _ in range(num_grades):
        student = random.choice(students)
        discipline = random.choice(disciplines)
        grade_val = random.choice([5, 4, 3, 2, 'Отлично', 'Хорошо', 'Удовлетворительно', 'Неуд'])
        grade = {
            "id": fake.uuid4(),
            "student_id": student['id'],
            "discipline_id": discipline['id'],
            "grade": str(grade_val),
            "date": fake.date_between(start_date='-1y', end_date='today').strftime('%Y-%m-%d')
        }
        grades.append(grade)
    return grades

def generate_schedule(groups, disciplines, teachers):
    schedule = []
    days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница']

    # Создаем маппинг: Имя дисциплины -> Список учителей, которые её ведут
    discipline_to_teachers = {}
    for disc in disciplines:
        disc_name = disc['name']
        possible_teachers = [t for t in teachers if disc_name in t['disciplines']]
        discipline_to_teachers[disc_name] = possible_teachers

    for group in groups:
        for day in days:
            lessons_count = random.randint(1, 4) # Пар может быть больше
            used_disciplines_ids = set()
            lessons = []

            for _ in range(lessons_count):
                # Выбираем дисциплину, которой еще не было в этот день у этой группы
                available_disciplines = [d for d in disciplines if d['id'] not in used_disciplines_ids]
                if not available_disciplines:
                    break

                discipline = random.choice(available_disciplines)
                used_disciplines_ids.add(discipline['id'])

                # Ищем учителя для этой дисциплины
                possible_teachers = discipline_to_teachers.get(discipline['name'], [])

                if possible_teachers:
                    teacher = random.choice(possible_teachers)
                else:
                    # Если нет конкретного учителя, берем случайного (fallback)
                    teacher = random.choice(teachers)
                    print(f"Warning: No specific teacher for {discipline['name']}, assigned random {teacher['name']}")

                lessons.append({
                    "discipline_id": discipline['id'],
                    "discipline_name": discipline['name'], # Сохраняем имя сразу, чтобы не джойнить потом
                    "teacher_id": teacher['id'],           # Важно: сохраняем ID учителя
                    "teacher_name": teacher['name'],       # И имя для удобства отображения
                    "room": random.randint(100, 300),
                })

            if lessons: # Добавляем только если есть уроки
                schedule.append({
                    "id": fake.uuid4(),
                    "group": group,
                    "day": day,
                    "lessons": lessons
                })

    return schedule

def generate_data():
    students = generate_students(20)
    teachers = generate_teachers(8) # Увеличим кол-во учителей
    disciplines = generate_disciplines()
    materials = generate_materials(disciplines, 30)
    groups = list(set(s['group'] for s in students))
    schedule = generate_schedule(groups, disciplines, teachers)
    grades = generate_grades(students, disciplines, 60)

    data = {
        "students": students,
        "teachers": teachers,
        "disciplines": disciplines,
        "materials": materials,
        "schedule": schedule,
        "grades": grades
    }

    with open('fixtures.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("Fixtures generated successfully.")

if __name__ == "__main__":
    generate_data()
