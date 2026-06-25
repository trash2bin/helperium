"""Тесты дисциплин — через AsyncDataServiceClient (HTTP)."""

from unittest.mock import AsyncMock, MagicMock, patch

from agent_tutor_sdk.contracts import Discipline
from agent_tutor_sdk.data_client import AsyncDataServiceClient


def _mock_response(status_code: int, json_data):
    """Вспомогательная: создаёт мок httpx.Response (sync) для return_value AsyncMock."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data
    return response


async def test_get_disciplines_for_student():
    """Дисциплины студента через HTTP."""
    client = AsyncDataServiceClient("http://mock")

    mock_disciplines = [
        {
            "id": "d1",
            "name": "Алгоритмы и структуры данных",
            "description": "Курс по алгоритмам",
        },
        {"id": "d2", "name": "Базы данных", "description": "Курс по SQL"},
    ]

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_disciplines)

        disciplines = await client.get_student_disciplines("s1")

    assert len(disciplines) == 2
    assert disciplines[0].name == "Алгоритмы и структуры данных"


async def test_get_disciplines_empty():
    """Пустой список для неизвестного студента."""
    client = AsyncDataServiceClient("http://mock")

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, [])

        disciplines = await client.get_student_disciplines("nonexistent")

    assert disciplines == []


async def test_get_disciplines_contains_valid_data():
    """Структура возвращаемых дисциплин."""
    client = AsyncDataServiceClient("http://mock")

    mock_disciplines = [
        {"id": "d1", "name": "Алгоритмы", "description": "Описание"},
    ]

    with patch.object(client, "_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = _mock_response(200, mock_disciplines)

        disciplines = await client.get_student_disciplines("s1")

    for discipline in disciplines:
        assert isinstance(discipline, Discipline)
        assert isinstance(discipline.id, str)
        assert isinstance(discipline.name, str)
        assert isinstance(discipline.description, str)