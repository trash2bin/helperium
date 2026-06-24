from agent_tutor_sdk.db.database import Database
from agent_tutor_sdk.db.models import Discipline


def test_database_initialization(test_db):
    """Test that the database initializes, creates schema, and loads seeds successfully."""
    assert test_db is not None
    # Check that we can query groups
    groups = test_db.fetch_all("SELECT * FROM groups")
    assert len(groups) > 0


def test_get_group(test_db):
    """Test retrieving groups by ID."""
    # Let's get a group ID from seed data
    row = test_db.fetch_one("SELECT id, name FROM groups LIMIT 1")
    assert row is not None
    group_id = row["id"]
    group_name = row["name"]

    group = test_db.get_group(group_id)
    assert group is not None
    assert group.id == group_id
    assert group.name == group_name

    # Non-existent group
    assert test_db.get_group("non-existent-id") is None


def test_get_student(test_db):
    """Test retrieving students by ID."""
    row = test_db.fetch_one("SELECT id, name FROM students LIMIT 1")
    assert row is not None
    student_id = row["id"]
    student_name = row["name"]

    student = test_db.get_student(student_id)
    assert student is not None
    assert student.id == student_id
    assert student.name == student_name
    assert student.group is not None

    # Non-existent student
    assert test_db.get_student("non-existent-id") is None


def test_get_id_student(test_db):
    """Test retrieving student by exact name."""
    row = test_db.fetch_one("SELECT name, id FROM students LIMIT 1")
    assert row is not None
    student_name = row["name"]
    student_id = row["id"]

    student = test_db.get_id_student(student_name)
    assert student is not None
    assert student.id == student_id
    assert student.name == student_name

    # Non-existent student or None
    assert test_db.get_id_student("Ivan Neizvestny") is None
    assert test_db.get_id_student(None) is None


def test_get_teacher_by_name(test_db):
    """Test retrieving teacher by name."""
    row = test_db.fetch_one("SELECT name FROM teachers LIMIT 1")
    assert row is not None
    teacher_name = row["name"]

    teacher = test_db.get_teacher_by_name(teacher_name)
    assert teacher is not None
    assert teacher.name == teacher_name
    assert isinstance(teacher.disciplines, list)

    # Non-existent teacher
    assert test_db.get_teacher_by_name("No Such Teacher") is None


def test_get_teacher_schedule(test_db):
    """Test retrieving a teacher's schedule."""
    # Find a teacher with lessons in schedule
    # In the schedule table, there are lessons with teacher names
    row = test_db.fetch_one("SELECT lessons_json FROM schedule LIMIT 5")
    assert row is not None

    # Let's get any schedule entry or teacher name from database
    teacher_row = test_db.fetch_one("SELECT name FROM teachers LIMIT 1")
    assert teacher_row is not None
    teacher_name = teacher_row["name"]

    schedule = test_db.get_teacher_schedule(teacher_name)
    assert isinstance(schedule, list)

    # Filtered by day
    schedule_day = test_db.get_teacher_schedule(teacher_name, day="Понедельник")
    assert isinstance(schedule_day, list)

    # Non-existent teacher
    assert test_db.get_teacher_schedule("Non Existent Teacher") == []


def test_get_schedule(test_db):
    """Test retrieving schedule for a group."""
    row = test_db.fetch_one("SELECT group_id, day FROM schedule LIMIT 1")
    assert row is not None
    group_id = row["group_id"]
    day = row["day"]

    schedule = test_db.get_schedule(group_id)
    assert len(schedule) > 0
    assert schedule[0].group.id == group_id

    schedule_day = test_db.get_schedule(group_id, day=day)
    assert len(schedule_day) > 0
    assert schedule_day[0].day == day


def test_get_disciplines(test_db):
    """Test retrieving disciplines for a student's group."""
    student_row = test_db.fetch_one("SELECT id FROM students LIMIT 1")
    assert student_row is not None
    student_id = student_row["id"]

    disciplines = test_db.get_disciplines(student_id)
    assert isinstance(disciplines, list)

    # Non-existent student
    assert test_db.get_disciplines("non-existent") == []


def test_get_discipline(test_db):
    """Test retrieving a discipline by ID."""
    row = test_db.fetch_one("SELECT id, name FROM disciplines LIMIT 1")
    assert row is not None
    disc_id = row["id"]
    disc_name = row["name"]

    discipline = test_db.get_discipline(disc_id)
    assert discipline is not None
    assert discipline.id == disc_id
    assert discipline.name == disc_name

    # Non-existent
    assert test_db.get_discipline("non-existent") is None


def test_get_all_disciplines(test_db):
    """Test retrieving all disciplines in database."""
    disciplines = test_db.get_all_disciplines()
    assert len(disciplines) > 0
    assert all(isinstance(d, Discipline) for d in disciplines)


def test_get_student_grades(test_db):
    """Test retrieving grades for a student."""
    student_row = test_db.fetch_one("SELECT id FROM students LIMIT 1")
    assert student_row is not None
    student_id = student_row["id"]

    # Get grades
    grades = test_db.get_student_grades(student_id)
    assert isinstance(grades, list)

    # Get grades with discipline_id filter if possible
    row_with_grade = test_db.fetch_one(
        "SELECT student_id, discipline_id FROM grades LIMIT 1"
    )
    if row_with_grade:
        s_id = row_with_grade["student_id"]
        d_id = row_with_grade["discipline_id"]
        grades_filtered = test_db.get_student_grades(s_id, discipline_id=d_id)
        assert len(grades_filtered) > 0
        assert all(g.discipline_id == d_id for g in grades_filtered)


def test_ping_and_context_manager(db_path):
    """Test ping method and using Database as a context manager."""
    with Database(db_path=db_path) as db:
        db.ping()  # should not raise
        # Commit / Rollback smoke test
        db.commit()
        db.rollback()

    # After exit, db connection should be closed
    assert db._closed is True
