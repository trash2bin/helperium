"""Тесты инструментов студента — через AsyncDataServiceClient (HTTP)."""

from unittest.mock import AsyncMock, MagicMock, patch

from agent_tutor_sdk.data_client import AsyncDataServiceClient


def _mock_response(status_code: int, json_data):
    """Вспомогательная: создаёт мок httpx.Response (sync) для return_value AsyncMock."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    return response


async def test_get_student():
    """Получение студента по ID через HTTP."""
    client = AsyncDataServiceClient("http://mock")

    mock_student = {
        "id": "s1",
        "full_name": "Иван Петров Иванович",
        "group": {"id": "g1", "name": "ИВТ-21", "speciality": "Информационные системы"},
        "course": 2,
    }

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_student)

        student = await client.get_student("s1")

    assert student is not None
    assert student.full_name == "Иван Петров Иванович"
    assert student.course == 2
    assert student.group is not None
    assert student.group.name == "ИВТ-21"


async def test_find_student_by_name():
    """Поиск студента по имени через HTTP."""
    client = AsyncDataServiceClient("http://mock")

    mock_student = {
        "id": "s1",
        "full_name": "Иван Петров Иванович",
        "group": None,
        "course": None,
    }

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_student)

        student = await client.find_student_by_name("Иван Петров Иванович")

    assert student is not None
    assert student.full_name == "Иван Петров Иванович"


async def test_get_student_not_found():
    """404 при ненайденном студенте."""
    client = AsyncDataServiceClient("http://mock")

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(404, None)

        student = await client.get_student("nonexistent")

    assert student is None


async def test_get_schedule():
    """Расписание группы через HTTP."""

    client = AsyncDataServiceClient("http://mock")

    mock_schedule = [
        {
            "id": "sc1",
            "group": {
                "id": "g1",
                "name": "ИВТ-21",
                "speciality": "Информационные системы",
            },
            "day": "Понедельник",
            "lessons": [
                {
                    "discipline_id": "d1",
                    "discipline_name": "Алгоритмы",
                    "teacher_name": "Оксана Ниловна",
                    "room": 101,
                }
            ],
        }
    ]

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_schedule)

        schedule = await client.get_group_schedule("g1")

    assert len(schedule) == 1
    assert schedule[0].day == "Понедельник"
    assert len(schedule[0].lessons) == 1
    assert schedule[0].lessons[0].teacher_name == "Оксана Ниловна"