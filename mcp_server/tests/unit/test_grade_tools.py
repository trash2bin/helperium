"""Тесты оценок — через AsyncDataServiceClient (HTTP)."""

from unittest.mock import AsyncMock, MagicMock, patch

from agent_tutor_sdk.data_client import AsyncDataServiceClient


def _mock_response(status_code: int, json_data):
    """Вспомогательная: создаёт мок httpx.Response (sync) для return_value AsyncMock."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    return response


async def test_get_student_grades():
    """Оценки студента через HTTP."""
    client = AsyncDataServiceClient("http://mock")

    mock_grades = [
        {
            "id": "gr1",
            "student_id": "s1",
            "discipline_id": "d1",
            "discipline_name": "Алгоритмы",
            "grade": "5",
            "date": "2026-01-15",
        },
        {
            "id": "gr2",
            "student_id": "s1",
            "discipline_id": "d2",
            "discipline_name": "Базы данных",
            "grade": "4",
            "date": "2026-02-20",
        },
    ]

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_grades)

        grades = await client.get_student_grades("s1")

    assert len(grades) == 2
    assert grades[0].value == "5"
    assert grades[0].discipline_name == "Алгоритмы"


async def test_get_student_grades_empty():
    """Пустой список оценок для неизвестного студента."""
    client = AsyncDataServiceClient("http://mock")

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, [])

        grades = await client.get_student_grades("nonexistent")

    assert grades == []


async def test_get_student_grades_with_discipline_filter():
    """Оценки студента с фильтром по дисциплине."""
    client = AsyncDataServiceClient("http://mock")

    mock_grades = [
        {
            "id": "gr1",
            "student_id": "s1",
            "discipline_id": "d1",
            "discipline_name": "Алгоритмы",
            "grade": "5",
            "date": "2026-01-15",
        }
    ]

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_grades)

        grades = await client.get_student_grades("s1", discipline_id="d1")

    assert len(grades) == 1
    assert grades[0].discipline_id == "d1"


async def test_get_student_grades_structure():
    """Структура возвращаемых оценок."""
    client = AsyncDataServiceClient("http://mock")

    mock_grades = [
        {
            "id": "gr1",
            "student_id": "s1",
            "discipline_id": "d1",
            "discipline_name": "Алгоритмы",
            "grade": "5",
            "date": "2026-01-15",
        }
    ]

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_grades)

        grades = await client.get_student_grades("s1")

    for grade in grades:
        assert isinstance(grade.id, str)
        assert isinstance(grade.student_id, str)
        assert isinstance(grade.discipline_id, str)
        assert isinstance(grade.discipline_name, str)
        assert isinstance(grade.value, str)
        assert isinstance(grade.date, str)