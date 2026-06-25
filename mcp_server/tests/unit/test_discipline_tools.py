"""Тесты дисциплин — через DataServiceClient (HTTP)."""

from unittest.mock import patch

from agent_tutor_sdk.data_client import DataServiceClient
from agent_tutor_sdk.contracts import Discipline


def test_get_disciplines_for_student():
    """Дисциплины студента через HTTP."""
    client = DataServiceClient("http://mock")

    mock_disciplines = [
        {"id": "d1", "name": "Алгоритмы и структуры данных", "description": "Курс по алгоритмам"},
        {"id": "d2", "name": "Базы данных", "description": "Курс по SQL"},
    ]

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_disciplines

        disciplines = client.get_student_disciplines("s1")

    assert len(disciplines) == 2
    assert disciplines[0].name == "Алгоритмы и структуры данных"


def test_get_disciplines_empty():
    """Пустой список для неизвестного студента."""
    client = DataServiceClient("http://mock")

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []

        disciplines = client.get_student_disciplines("nonexistent")

    assert disciplines == []


def test_get_disciplines_contains_valid_data():
    """Структура возвращаемых дисциплин."""
    client = DataServiceClient("http://mock")

    mock_disciplines = [
        {"id": "d1", "name": "Алгоритмы", "description": "Описание"},
    ]

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_disciplines

        disciplines = client.get_student_disciplines("s1")

    for discipline in disciplines:
        assert isinstance(discipline, Discipline)
        assert isinstance(discipline.id, str)
        assert isinstance(discipline.name, str)
        assert isinstance(discipline.description, str)
