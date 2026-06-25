"""Тесты оценок — через DataServiceClient (HTTP)."""

from unittest.mock import patch

from agent_tutor_sdk.data_client import DataServiceClient


def test_get_student_grades():
    """Оценки студента через HTTP."""
    client = DataServiceClient("http://mock")

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

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_grades

        grades = client.get_student_grades("s1")

    assert len(grades) == 2
    assert grades[0].value == "5"
    assert grades[0].discipline_name == "Алгоритмы"


def test_get_student_grades_empty():
    """Пустой список оценок для неизвестного студента."""
    client = DataServiceClient("http://mock")

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = []

        grades = client.get_student_grades("nonexistent")

    assert grades == []


def test_get_student_grades_with_discipline_filter():
    """Оценки студента с фильтром по дисциплине."""
    client = DataServiceClient("http://mock")

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

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_grades

        grades = client.get_student_grades("s1", discipline_id="d1")

    assert len(grades) == 1
    assert grades[0].discipline_id == "d1"


def test_get_student_grades_structure():
    """Структура возвращаемых оценок."""
    client = DataServiceClient("http://mock")

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

    with patch.object(client, "_get") as mock_get:
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = mock_grades

        grades = client.get_student_grades("s1")

    for grade in grades:
        assert isinstance(grade.id, str)
        assert isinstance(grade.student_id, str)
        assert isinstance(grade.discipline_id, str)
        assert isinstance(grade.discipline_name, str)
        assert isinstance(grade.value, str)
        assert isinstance(grade.date, str)
