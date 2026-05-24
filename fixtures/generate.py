from faker import Faker
import json
import random

fake = Faker()

# Generate students
def generate_students(num_students=10):
    students = []
    for _ in range(num_students):
        student = {
            "id": fake.uuid4(),
            "name": fake.name(),
            "group": fake.random_element(elements=('CS101', 'CS102', 'CS103', 'CS104', 'CS105')),
            "course": random.randint(1, 4),
            "specialty": fake.random_element(elements=('Computer Science', 'Software Engineering', 'Data Science', 'Cybersecurity'))
        }
        students.append(student)
    return students

# Generate disciplines
def generate_disciplines(num_disciplines=5):
    disciplines = []
    for _ in range(num_disciplines):
        discipline = {
            "id": fake.uuid4(),
            "name": fake.random_element(elements=('Algorithms', 'Databases', 'Web Development', 'Machine Learning', 'Network Security')),
            "description": fake.text(max_nb_chars=200)
        }
        disciplines.append(discipline)
    return disciplines

# Generate materials
def generate_materials(disciplines, num_materials=20):
    materials = []
    for _ in range(num_materials):
        material = {
            "id": fake.uuid4(),
            "discipline_id": random.choice(disciplines)['id'],
            "type": fake.random_element(elements=('Lecture', 'Methodology', 'Task')),
            "content": fake.text(max_nb_chars=500)
        }
        materials.append(material)
    return materials

# Generate schedule
def generate_schedule(groups, disciplines, num_entries=30):
    schedule = []
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
    for _ in range(num_entries):
        entry = {
            "id": fake.uuid4(),
            "group": random.choice(groups),
            "day": random.choice(days),
            "time": fake.time(),
            "discipline_id": random.choice(disciplines)['id'],
            "room": fake.random_int(min=100, max=300)
        }
        schedule.append(entry)
    return schedule

# Generate data
def generate_data():
    students = generate_students()
    disciplines = generate_disciplines()
    materials = generate_materials(disciplines)
    groups = list(set(student['group'] for student in students))
    schedule = generate_schedule(groups, disciplines)

    data = {
        "students": students,
        "disciplines": disciplines,
        "materials": materials,
        "schedule": schedule
    }

    with open('fixtures.json', 'w') as f:
        json.dump(data, f, indent=2)

if __name__ == "__main__":
    generate_data()
