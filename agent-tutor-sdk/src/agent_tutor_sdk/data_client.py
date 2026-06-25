"""HTTP-клиент для data-service (Go-сервис доступа к БД).

Используется MCP-сервером и API для вызовов к data-service через HTTP.
Контракт описан в specs/data-service.openapi.yaml.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import quote

import httpx

from agent_tutor_sdk.contracts import (
    Discipline,
    Grade,
    ScheduleEntry,
    Student,
    Teacher,
)

logger = logging.getLogger(__name__)

DATA_SERVICE_URL: str = os.environ.get(
    "DATA_SERVICE_URL", "http://127.0.0.1:8084"
)


class DataServiceClient:
    """Тонкий HTTP-клиент к Go data-service.

    Не содержит SQL, не знает имён таблиц или колонок.
    Все методы возвращают контрактные Pydantic-модели.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 10.0,
    ):
        self.base_url = (base_url or DATA_SERVICE_URL).rstrip("/")
        self.timeout = timeout
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def _get(self, path: str) -> httpx.Response:
        return self.client.get(f"{self.base_url}{path}")

    # ── Health ──

    def health(self) -> dict[str, str]:
        resp = self._get("/health")
        resp.raise_for_status()
        return resp.json()

    # ── Stats ──

    def get_stats(self) -> dict[str, int]:
        resp = self._get("/stats")
        resp.raise_for_status()
        return resp.json()

    # ── Students ──

    def get_student(self, student_id: str) -> Student | None:
        resp = self._get(f"/students/{quote(student_id, safe='')}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Student(**resp.json())

    def find_student_by_name(self, name: str) -> Student | None:
        resp = self._get(f"/students?name={quote(name)}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Student(**resp.json())

    def get_student_disciplines(self, student_id: str) -> list[Discipline]:
        resp = self._get(f"/students/{quote(student_id, safe='')}/disciplines")
        resp.raise_for_status()
        return [Discipline(**d) for d in resp.json()]

    def get_student_grades(
        self, student_id: str, discipline_id: str | None = None
    ) -> list[Grade]:
        path = f"/students/{quote(student_id, safe='')}/grades"
        if discipline_id:
            path += f"?discipline_id={quote(discipline_id)}"
        resp = self._get(path)
        resp.raise_for_status()
        return [Grade(**g) for g in resp.json()]

    # ── Teachers ──

    def find_teacher_by_name(self, name: str) -> Teacher | None:
        resp = self._get(f"/teachers?name={quote(name)}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return Teacher(**resp.json())

    def get_teacher_schedule(
        self, teacher_name: str, day: str | None = None
    ) -> list[ScheduleEntry]:
        path = f"/teachers/{quote(teacher_name)}/schedule"
        if day:
            path += f"?day={quote(day)}"
        resp = self._get(path)
        resp.raise_for_status()
        return [ScheduleEntry(**s) for s in resp.json()]

    # ── Schedule ──

    def get_group_schedule(
        self, group_id: str, day: str | None = None
    ) -> list[ScheduleEntry]:
        path = f"/groups/{quote(group_id, safe='')}/schedule"
        if day:
            path += f"?day={quote(day)}"
        resp = self._get(path)
        resp.raise_for_status()
        return [ScheduleEntry(**s) for s in resp.json()]

    # ── Disciplines ──

    def get_all_disciplines(self) -> list[Discipline]:
        resp = self._get("/disciplines")
        resp.raise_for_status()
        return [Discipline(**d) for d in resp.json()]

    # ── List-all (для /api/data overview) ──

    def get_all_students(self) -> list[Student]:
        resp = self._get("/students")
        resp.raise_for_status()
        return [Student(**s) for s in resp.json()]

    def get_all_teachers(self) -> list[Teacher]:
        resp = self._get("/teachers")
        resp.raise_for_status()
        return [Teacher(**t) for t in resp.json()]

    def get_all_schedule(self) -> list[ScheduleEntry]:
        resp = self._get("/schedule")
        resp.raise_for_status()
        return [ScheduleEntry(**s) for s in resp.json()]

    def get_all_grades(self) -> list[Grade]:
        resp = self._get("/grades")
        resp.raise_for_status()
        return [Grade(**g) for g in resp.json()]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


# Глобальный экземпляр (ленивый, создаётся при первом использовании)
_client: DataServiceClient | None = None


def get_data_service_client() -> DataServiceClient:
    global _client
    if _client is None:
        _client = DataServiceClient()
    return _client
