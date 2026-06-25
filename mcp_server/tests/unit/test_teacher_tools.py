"""Тесты инструментов преподавателя — через DataServiceClient (HTTP)."""

from unittest.mock import patch

from agent_tutor_sdk.data_client import DataServiceClient


def test_get_teacher_by_name():
    """Поиск преподавателя по имени через HTTP."""
    client = DataServiceClient("http://mock")

    mock_teacher = {
        "id": "t1",
        "full_name": "Оксана Ниловна Константинова",
        "disciplines": ["Алгоритмы", "Базы данных"],
    }

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_teacher

        teacher = client.find_teacher_by_name("Оксана Ниловна Константинова")

    assert teacher is not None
    assert teacher.full_name == "Оксана Ниловна Константинова"
    assert "Алгоритмы" in teacher.disciplines


def test_get_teacher_by_name_not_found():
    """404 при ненайденном преподавателе."""
    client = DataServiceClient("http://mock")

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 404

        teacher = client.find_teacher_by_name("Несуществующий")

    assert teacher is None


def test_get_teacher_schedule():
    """Расписание преподавателя через HTTP."""
    client = DataServiceClient("http://mock")

    mock_schedule = [
        {
            "id": "sc1",
            "group": {"id": "g1", "name": "ИВТ-21", "speciality": "ИС"},
            "day": "Понедельник",
            "lessons": [
                {
                    "discipline_id": "d1",
                    "discipline_name": "Алгоритмы",
                    "teacher_name": "Оксана Ниловна Константинова",
                    "room": 101,
                }
            ],
        }
    ]

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_schedule

        schedule = client.get_teacher_schedule("Оксана Ниловна Константинова")

    assert len(schedule) == 1
    assert schedule[0].day == "Понедельник"


def test_get_teacher_schedule_with_day():
    """Расписание преподавателя с фильтром по дню."""
    client = DataServiceClient("http://mock")

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []

        schedule = client.get_teacher_schedule("Оксана Ниловна Константинова", day="Вторник")

    assert schedule == []


def test_get_teacher_schedule_nonexistent_teacher():
    """Расписание для несуществующего преподавателя — пустой список."""
    client = DataServiceClient("http://mock")

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []

        schedule = client.get_teacher_schedule("Несуществующий")

    assert schedule == []
