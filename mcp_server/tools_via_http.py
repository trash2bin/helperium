"""MCP-инструменты через HTTP к data-service (Go).

Не содержат SQL, не знают имён таблиц или колонок.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from agent_tutor_sdk.data_client import get_data_service_client

logger = logging.getLogger(__name__)

# Ленивый клиент — создаётся при первом вызове любого инструмента
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = get_data_service_client()
        logger.info("DataServiceClient initialized (HTTP to Go data-service)")
    return _client


# ══════════════════════════════════════════════════════════════════════
# СТУДЕНТ
# ══════════════════════════════════════════════════════════════════════


def _find_student_by_name(name: str) -> Optional[Any]:
    """Найти студента по имени (через HTTP к data-service)."""
    return _get_client().find_student_by_name(name)


def _get_student(student_id: str) -> Optional[Any]:
    """Получить карточку студента по ID (через HTTP к data-service)."""
    return _get_client().get_student(student_id)


# ══════════════════════════════════════════════════════════════════════
# РАСПИСАНИЕ
# ══════════════════════════════════════════════════════════════════════


def _get_schedule(group_id: str, day: Optional[str] = None) -> List[Any]:
    """Расписание группы (через HTTP к data-service)."""
    return _get_client().get_group_schedule(group_id, day)


# ══════════════════════════════════════════════════════════════════════
# ДИСЦИПЛИНЫ И ОЦЕНКИ
# ══════════════════════════════════════════════════════════════════════


def _get_disciplines(student_id: str) -> List[Any]:
    """Список дисциплин студента (через HTTP к data-service)."""
    return _get_client().get_student_disciplines(student_id)


def _get_student_grades(
    student_id: str, discipline_id: Optional[str] = None
) -> List[Any]:
    """Оценки студента (через HTTP к data-service)."""
    return _get_client().get_student_grades(student_id, discipline_id)


# ══════════════════════════════════════════════════════════════════════
# ПРЕПОДАВАТЕЛЬ
# ══════════════════════════════════════════════════════════════════════


def _get_teacher_by_name(name: str) -> Optional[Any]:
    """Найти преподавателя по имени (через HTTP к data-service)."""
    return _get_client().find_teacher_by_name(name)


def _get_teacher_schedule(
    teacher_name: str, day: Optional[str] = None
) -> List[Any]:
    """Расписание преподавателя (через HTTP к data-service)."""
    return _get_client().get_teacher_schedule(teacher_name, day)


# ══════════════════════════════════════════════════════════════════════
# HEALTH (для data-service)
# ══════════════════════════════════════════════════════════════════════


async def _health_db_status() -> dict:
    """Проверить статус data-service."""
    try:
        health = _get_client().health()
        return {"status": health.get("status", "ok"), "error": None}
    except Exception as e:
        return {"status": "error", "error": str(e)}
