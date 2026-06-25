"""Repository for demo data access.

Все данные университета — через HTTP к Go data-service (async).
Адаптирует контрактные модели к формату, который ожидает веб-интерфейс
(плоские поля name/group_name/speciality вместо full_name/group.name).
"""

from __future__ import annotations

import logging
from typing import Any

from agent_tutor_sdk.data_client import AsyncDataServiceClient
from agent_tutor_sdk.rag.client import RagClient

logger = logging.getLogger(__name__)


class DemoDataRepository:
    """Repository for demo data access. Ходит через HTTP к Go data-service и RAG.

    Все методы async — не блокируют event loop. Использует :class:`AsyncDataServiceClient`
    для data-service и :class:`RagClient` (async) для RAG.
    """

    def __init__(
        self,
        data_client: AsyncDataServiceClient | None = None,
        rag_client: RagClient | None = None,
    ) -> None:
        self._data_client = data_client
        self._rag_client = rag_client

    @property
    def data_client(self) -> AsyncDataServiceClient:
        if self._data_client is None:
            self._data_client = AsyncDataServiceClient()
        return self._data_client

    @property
    def rag_client(self) -> RagClient:
        if self._rag_client is None:
            self._rag_client = RagClient()
        return self._rag_client

    async def overview(self) -> dict[str, Any]:
        return {
            "stats": await self._stats(),
            "students": await self._students(),
            "teachers": await self._teachers(),
            "disciplines": await self._disciplines(),
            "schedule": await self._schedule(),
            "documents": await self._documents(),
            "grades": await self._grades(),
        }

    async def _stats(self) -> dict[str, int]:
        try:
            s = await self.data_client.get_stats()
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
            return {
                "students": -1,
                "teachers": -1,
                "disciplines": -1,
                "documents": -1,
                "grades": -1,
                "schedule": -1,
            }

    async def _students(self) -> list[dict[str, Any]]:
        """Студенты в старом плоском формате для совместимости с web UI."""
        try:
            students = await self.data_client.get_all_students()
            return [
                {
                    "id": s.id,
                    "name": s.full_name,  # full_name → name
                    "course": s.course,
                    "group_name": s.group.name if s.group else "",
                    "speciality": s.group.speciality if s.group else "",
                }
                for s in students
            ]
        except Exception as exc:
            logger.warning("data-service /students: %s", exc)
            return []

    async def _teachers(self) -> list[dict[str, Any]]:
        """Преподаватели в старом плоском формате."""
        try:
            teachers = await self.data_client.get_all_teachers()
            return [
                {
                    "id": t.id,
                    "name": t.full_name,  # full_name → name
                    "disciplines": t.disciplines,
                }
                for t in teachers
            ]
        except Exception as exc:
            logger.warning("data-service /teachers: %s", exc)
            return []

    async def _disciplines(self) -> list[dict[str, Any]]:
        try:
            disciplines = await self.data_client.get_all_disciplines()
            return [d.model_dump(mode="json") for d in disciplines]
        except Exception as exc:
            logger.warning("data-service /disciplines: %s", exc)
            return []

    async def _schedule(self) -> list[dict[str, Any]]:
        """Расписание в старом плоском формате."""
        try:
            entries = await self.data_client.get_all_schedule()
            return [
                {
                    "id": entry.id,
                    "day": entry.day,
                    "group_name": entry.group.name if entry.group else "",
                    "lessons": [
                        lesson.model_dump(mode="json") for lesson in entry.lessons
                    ],
                }
                for entry in entries
            ]
        except Exception as exc:
            logger.warning("data-service /schedule: %s", exc)
            return []

    async def _documents(self) -> list[dict[str, Any]]:
        """Документы из RAG-сервиса."""
        try:
            docs = await self.rag_client.list_documents()
            return [d.model_dump(mode="json") for d in docs]
        except Exception as exc:
            logger.warning("rag /documents/list: %s", exc)
            return []

    async def _grades(self) -> list[dict[str, Any]]:
        """Оценки из data-service (уже с student_name и grade)."""
        try:
            grades = await self.data_client.get_all_grades()
            return [g.model_dump(mode="json", by_alias=True) for g in grades]
        except Exception as exc:
            logger.warning("data-service /grades: %s", exc)
            return []


data_repository = DemoDataRepository()
