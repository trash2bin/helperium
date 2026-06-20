"""HTTP DTO для Core API сервиса.

Определяет контракты взаимодействия между Web-фронтендом и API-сервером.
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# === Запросы ===

class ChatRequest(BaseModel):
    """Запрос на начало или продолжение чата."""

    message: str = Field(..., min_length=1, description="Текст сообщения от пользователя")
    session_id: Optional[str] = Field(default="default", description="ID сессии для сохранения истории")


# === Ответы ===

class HealthResponse(BaseModel):
    """Состояние API сервиса и его связей с LLM."""

    api: str = "ok"
    ollama: dict = Field(..., description="Статус подключения к LLM провайдеру")


class DataOverviewResponse(BaseModel):
    """Обзор доступных демонстрационных данных."""

    data: dict = Field(..., description="Сводная информация по базе данных вуза")


class BacklogListResponse(BaseModel):
    """Список всех сессий в бэклоге."""

    sessions: List[dict] = Field(..., description="Список метаданных сессий")


class BacklogDetailResponse(BaseModel):
    """Записи конкретной сессии из бэклога."""

    records: List[dict] = Field(..., description="Список событий/записей сессии")
    session_id: str
    count: int


class SessionHistoryResponse(BaseModel):
    """История сообщений чата для сессии."""

    messages: List[dict] = Field(..., description="Список сообщений (role, content)")
