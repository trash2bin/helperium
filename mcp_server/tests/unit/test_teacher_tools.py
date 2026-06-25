"""Тесты инструментов преподавателя — через AsyncDataServiceClient (HTTP)."""

from unittest.mock import AsyncMock, MagicMock, patch

from agent_tutor_sdk.data_client import AsyncDataServiceClient


def _mock_response(status_code: int, json_data):
    """Вспомогательная: создаёт мок httpx.Response (sync) для return_value AsyncMock."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    return response


async def test_get_teacher_by_name():
    """Поиск преподавателя по имени через HTTP."""
    client = AsyncDataServiceClient("http://mock")

    mock_teacher = {
        "id": "t1",
        "full_name": "Оксана Ниловна Константинова",
        "disciplines": ["Алгоритмы", "Базы данных"],
    }

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_teacher)

        teacher = await client.find_teacher_by_name("Оксана Ниловна Константинова")

    assert teacher is not None
    assert teacher.full_name == "Оксана Ниловна Константинова"
    assert "Алгоритмы" in teacher.disciplines


async def test_get_teacher_by_name_not_found():
    """404 при ненайденном преподавателе."""
    client = AsyncDataServiceClient("http://mock")

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(404, None)

        teacher = await client.find_teacher_by_name("Несуществующий")

    assert teacher is None


async def test_get_teacher_schedule():
    """Расписание преподавателя через HTTP."""
    client = AsyncDataServiceClient("http://mock")

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

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_schedule)

        schedule = await client.get_teacher_schedule("Оксана Ниловна Константинова")

    assert len(schedule) == 1
    assert schedule[0].day == "Понедельник"


async def test_get_teacher_schedule_with_day():
    """Расписание преподавателя с фильтром по дню."""
    client = AsyncDataServiceClient("http://mock")

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, [])

        schedule = await client.get_teacher_schedule(
            "Оксана Ниловна Константинова", day="Вторник"
        )

    assert schedule == []


async def test_get_teacher_schedule_nonexistent_teacher():
    """Расписание для несуществующего преподавателя — пустой список."""
    client = AsyncDataServiceClient("http://mock")

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, [])

        schedule = await client.get_teacher_schedule("Несуществующий")

    assert schedule == []