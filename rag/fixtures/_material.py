"""Dev-only модели для генерации учебных материалов.

Не являются частью публичного контракта RAG-сервиса (HTTP/OpenAPI).
Используются только в CLI-инструментах `agent-generate` и при заполнении
тестовых данных фикстурами.
"""

from __future__ import annotations

from pydantic import BaseModel


class Material(BaseModel):
    """Учебный материал (документ, представленный как материал дисциплины)."""

    id: str
    discipline_id: str
    type: str
    title: str
    file_name: str
    source_path: str
    mime_type: str
    content: str = ""
