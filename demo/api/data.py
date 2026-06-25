"""Repository for demo data access.

Все данные университета — через HTTP к Go data-service.
Адаптирует контрактные модели к формату, который ожидает веб-интерфейс
(плоские поля name/group_name/speciality вместо full_name/group.name).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DemoDataRepository:
    """Repository for demo data access. Ходит через HTTP к Go data-service."""

    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from agent_tutor_sdk.data_client import get_data_service_client
            self._client = get_data_service_client()
        return self._client

    def overview(self) -> dict[str, Any]:
        return {
            "stats": self._stats(),
            "students": self._students(),
            "teachers": self._teachers(),
            "disciplines": self._disciplines(),
            "schedule": self._schedule(),
            "documents": self._documents(),
            "grades": self._grades(),
        }

    def _stats(self) -> dict[str, int]:
        try:
            s = self.client.get_stats()
            return {
                "students": s.get("students", -1),
                "teachers": s.get("teachers", -1),
                "disciplines": s.get("disciplines", -1),
                "documents": s.get("documents", -1),
                "grades": s.get("grades", -1),
                "schedule": s.get("schedule", -1),
            }
        except Exception as exc:
            logger.warning("data-service /stats: %s", exc)
            return {"students": -1, "teachers": -1, "disciplines": -1,
                    "documents": -1, "grades": -1, "schedule": -1}

    def _students(self) -> list[dict[str, Any]]:
        """Студенты в старом плоском формате для совместимости с web UI."""
        try:
            return [
                {
                    "id": s.id,
                    "name": s.full_name,                          # full_name → name
                    "course": s.course,
                    "group_name": s.group.name if s.group else "",
                    "speciality": s.group.speciality if s.group else "",
                }
                for s in self.client.get_all_students()
            ]
        except Exception as exc:
            logger.warning("data-service /students: %s", exc)
            return []

    def _teachers(self) -> list[dict[str, Any]]:
        """Преподаватели в старом плоском формате."""
        try:
            return [
                {
                    "id": t.id,
                    "name": t.full_name,                         # full_name → name
                    "disciplines": t.disciplines,
                }
                for t in self.client.get_all_teachers()
            ]
        except Exception as exc:
            logger.warning("data-service /teachers: %s", exc)
            return []

    def _disciplines(self) -> list[dict[str, Any]]:
        try:
            return [d.model_dump(mode="json") for d in self.client.get_all_disciplines()]
        except Exception as exc:
            logger.warning("data-service /disciplines: %s", exc)
            return []

    def _schedule(self) -> list[dict[str, Any]]:
        """Расписание в старом плоском формате."""
        try:
            result = []
            for entry in self.client.get_all_schedule():
                result.append({
                    "id": entry.id,
                    "day": entry.day,
                    "group_name": entry.group.name if entry.group else "",
                    "lessons": [lesson.model_dump(mode="json") for lesson in entry.lessons],
                })
            return result
        except Exception as exc:
            logger.warning("data-service /schedule: %s", exc)
            return []

    def _documents(self) -> list[dict[str, Any]]:
        """Документы из RAG-сервиса."""
        try:
            from agent_tutor_sdk.rag.client import RagClient
            rag = RagClient()
            docs = rag.list_documents_sync()
            return [d.model_dump(mode="json") for d in docs]
        except Exception as exc:
            logger.warning("rag /documents/list: %s", exc)
            return []

    def _grades(self) -> list[dict[str, Any]]:
        """Оценки из data-service (уже с student_name и grade)."""
        try:
            return [g.model_dump(mode="json", by_alias=True) for g in self.client.get_all_grades()]
        except Exception as exc:
            logger.warning("data-service /grades: %s", exc)
            return []


data_repository = DemoDataRepository()
