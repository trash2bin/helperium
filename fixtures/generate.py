from faker import Faker
import json
import random

fake = Faker('ru_RU')  # ← вот и всё изменение для имён( -ZeMa-)) )

# Пул русских текстов для описаний и контента
TEXTS = [
    "Данная дисциплина охватывает основные теоретические и практические аспекты.",
    "Курс предназначен для студентов, изучающих современные информационные технологии.",
    "В рамках дисциплины рассматриваются ключевые алгоритмы и методы решения задач.",
    "Материал включает лекции, лабораторные работы и самостоятельные задания.",
    "Освоение курса позволит применять полученные знания в профессиональной деятельности.",
]

def ru_text(max_chars=200):
    result = ""
    while len(result) < max_chars // 2:
        result += random.choice(TEXTS) + " "
    return result[:max_chars].strip()


def generate_students(num_students=10):
    students = []
    for _ in range(num_students):
        is_male = random.choice([True, False])
        student = {
            "id": fake.uuid4(),
            "name": fake.name_male() if is_male else fake.name_female(),
            "group": fake.random_element(elements=('ИВТ-21', 'ПИ-20', 'ИБ-22', 'ПМ-21', 'ИС-20')),
            "course": random.randint(1, 4),
            "specialty": fake.random_element(elements=(
                'Информационные системы и технологии',
                'Программная инженерия',
                'Информационная безопасность',
                'Прикладная математика',
                'Киберфизические системы'
            ))
        }
        students.append(student)
    return students


def generate_disciplines(num_disciplines=5):
    disciplines = []
    for _ in range(num_disciplines):
        discipline = {
            "id": fake.uuid4(),
            "name": fake.random_element(elements=(
                'Алгоритмы и структуры данных', 'Базы данных', 'Веб-технологии',
                'Машинное обучение', 'Компьютерные сети', 'Операционные системы',
                'Компьютерная графика', 'Теория алгоритмов', 'Криптография', 'Искусственный интеллект'
            )),
            "description": ru_text(200)  # ← русский текст
        }
        disciplines.append(discipline)
    return disciplines


def generate_materials(disciplines, num_materials=20):
    materials = []
    for _ in range(num_materials):
        material = {
            "id": fake.uuid4(),
            "discipline_id": random.choice(disciplines)['id'],
            "type": fake.random_element(elements=('Лекция', 'Методичка', 'Задание')),
            "content": ru_text(500)  # ← русский текст
        }
        materials.append(material)
    return materials


def generate_grades(students, disciplines, num_grades=50):
    grades = []
    for _ in range(num_grades):
        grade = {
            "id": fake.uuid4(),
            "student_id": random.choice(students)['id'],
            "discipline_id": random.choice(disciplines)['id'],
            "grade": random.choice([5, 4, 3, 2, 'Отлично', 'Хорошо', 'Удовлетворительно', 'Неудовлетворительно', 'Зачтено', 'Не зачтено']),
            "date": fake.date_between(start_date='-1y', end_date='today').strftime('%Y-%m-%d')
        }
        grades.append(grade)
    return grades


def generate_schedule(groups, disciplines):
    schedule = []

    days = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница']

    for group in groups:
        for day in days:
            lessons_count = random.randint(1, 3)

            used_disciplines = set()
            lessons = []

            for _ in range(lessons_count):
                discipline = random.choice(disciplines)

                while discipline['id'] in used_disciplines:
                    discipline = random.choice(disciplines)

                used_disciplines.add(discipline['id'])

                lessons.append({
                    "discipline_id": discipline['id'],
                    "room": fake.random_int(min=100, max=300)
                })

            schedule.append({
                "id": fake.uuid4(),
                "group": group,
                "day": day,
                "lessons": lessons
            })

    return schedule


def generate_data():
    students = generate_students()
    disciplines = generate_disciplines()
    materials = generate_materials(disciplines)
    groups = list(set(s['group'] for s in students))
    schedule = generate_schedule(groups, disciplines)
    grades = generate_grades(students, disciplines)

    data = {
        "students": students,
        "disciplines": disciplines,
        "materials": materials,
        "schedule": schedule,
        "grades": grades
    }

    with open('fixtures.json', 'w', encoding='utf-8') as f:  # ← encoding важен для кириллицы
        json.dump(data, f, indent=2, ensure_ascii=False)       # ← ensure_ascii=False тоже


if __name__ == "__main__":
    generate_data()
